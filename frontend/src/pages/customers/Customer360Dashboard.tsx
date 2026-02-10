// ============================================================================
// IMS 2.0 - Customer 360 Dashboard
// ============================================================================
// Enterprise CRM: Full 360-degree customer view with multi-tab analytics,
// interaction history, loyalty management, and prescription lifecycle tracking

import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  User,
  Phone,
  Mail,
  MapPin,
  Award,
  Eye,
  ShoppingCart,
  Settings,
  Loader2,
  AlertCircle,
  Calendar,
  TrendingUp,
  Gift,
  Clock,
  CheckCircle,
  AlertTriangle,
  MessageCircle as MessageCircleIcon,
  PhoneCall,
} from 'lucide-react';
import type { Customer } from '../../types';
import { customerApi, orderApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

type Customer360Tab = 'overview' | 'prescriptions' | 'orders' | 'interactions' | 'loyalty' | 'preferences';

type LoyaltyTier = 'Bronze' | 'Silver' | 'Gold' | 'Platinum' | 'Diamond';

interface CustomerStats {
  totalLifetimeValue: number;
  totalOrders: number;
  lastOrderDate: string | null;
  lastOrderAmount: number | null;
  customerSinceDate: string;
  preferredStore: string;
  averageOrderValue: number;
  visitFrequency: number;
  referralCount: number;
  activeLoans: number;
}

interface InteractionRecord {
  id: string;
  type: 'call' | 'sms' | 'email' | 'whatsapp' | 'in_person';
  date: string;
  notes: string;
  duration?: number;
  initiatedBy: string;
}

interface PrescriptionData {
  id: string;
  customerId: string;
  testDate: string;
  rightEyeSph?: number;
  rightEyeCyl?: number;
  rightEyeAxis?: number;
  leftEyeSph?: number;
  leftEyeCyl?: number;
  leftEyeAxis?: number;
  doctorName?: string;
  renewalStatus: 'current' | 'upcoming' | 'expired';
  daysUntilRenewal?: number;
}

interface LoyaltyData {
  tier: LoyaltyTier;
  points: number;
  pointsToNextTier: number;
  redeemedPoints: number;
  totalPointsEarned: number;
  memberSince: string;
  birthdayMonth?: number;
}

export function Customer360Dashboard() {
  const { customerId } = useParams<{ customerId: string }>();
  const navigate = useNavigate();
  const toast = useToast();

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [prescriptions, setPrescriptions] = useState<PrescriptionData[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [interactions, setInteractions] = useState<InteractionRecord[]>([]);
  const [stats, setStats] = useState<CustomerStats | null>(null);
  const [loyaltyData, setLoyaltyData] = useState<LoyaltyData | null>(null);
  const [activeTab, setActiveTab] = useState<Customer360Tab>('overview');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!customerId) {
      navigate('/customers');
      return;
    }
    loadCustomerData();
  }, [customerId]);

  const loadCustomerData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Fetch customer details
      const customerData = await customerApi.getCustomer(customerId!);
      setCustomer(customerData);

      // Fetch orders
      const ordersData = await orderApi.getOrders({ customerId });
      setOrders(ordersData || []);

      // Calculate stats
      const totalLTV = ordersData?.reduce((sum: number, order: any) => sum + (order.total_amount || 0), 0) || 0;
      const calculatedStats: CustomerStats = {
        totalLifetimeValue: totalLTV,
        totalOrders: ordersData?.length || 0,
        lastOrderDate: ordersData?.[0]?.order_date || null,
        lastOrderAmount: ordersData?.[0]?.total_amount || null,
        customerSinceDate: customerData.created_at,
        preferredStore: customerData.store_id || 'Main Store',
        averageOrderValue: (ordersData?.length || 0) > 0 ? totalLTV / (ordersData?.length || 1) : 0,
        visitFrequency: Math.round((ordersData?.length || 0) / Math.max(1, monthsAsSinceDate(customerData.created_at))),
        referralCount: 0,
        activeLoans: 0,
      };
      setStats(calculatedStats);

      // Set loyalty data
      const loyaltyTier = calculateLoyaltyTier(calculatedStats.totalLifetimeValue);
      setLoyaltyData({
        tier: loyaltyTier,
        points: Math.floor(calculatedStats.totalLifetimeValue),
        pointsToNextTier: getPointsToNextTier(loyaltyTier, calculatedStats.totalLifetimeValue),
        redeemedPoints: 0,
        totalPointsEarned: Math.floor(calculatedStats.totalLifetimeValue),
        memberSince: customerData.created_at,
      });

      // Generate mock prescription data
      setPrescriptions(generateMockPrescriptions(customerId!));

      // Generate mock interaction data
      setInteractions(generateMockInteractions());

      toast.success('Customer data loaded successfully');
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load customer data';
      setError(errorMessage);
      toast.error(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const monthsAsSinceDate = (dateString: string): number => {
    const date = new Date(dateString);
    const now = new Date();
    return (now.getFullYear() - date.getFullYear()) * 12 + (now.getMonth() - date.getMonth());
  };

  const calculateLoyaltyTier = (lifetimeValue: number): LoyaltyTier => {
    if (lifetimeValue >= 100000) return 'Diamond';
    if (lifetimeValue >= 50000) return 'Platinum';
    if (lifetimeValue >= 25000) return 'Gold';
    if (lifetimeValue >= 10000) return 'Silver';
    return 'Bronze';
  };

  const getPointsToNextTier = (tier: LoyaltyTier, currentValue: number): number => {
    const thresholds = {
      Bronze: 10000,
      Silver: 25000,
      Gold: 50000,
      Platinum: 100000,
      Diamond: 100000,
    };
    const nextThreshold = thresholds[tier];
    return Math.max(0, nextThreshold - currentValue);
  };

  const generateMockPrescriptions = (customerId: string): PrescriptionData[] => {
    return [
      {
        id: `rx-1`,
        customerId,
        testDate: new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString(),
        rightEyeSph: 1.5,
        rightEyeCyl: -0.5,
        rightEyeAxis: 90,
        leftEyeSph: 1.75,
        leftEyeCyl: -0.75,
        leftEyeAxis: 88,
        doctorName: 'Dr. Raj Kumar',
        renewalStatus: 'upcoming',
        daysUntilRenewal: 20,
      },
      {
        id: `rx-2`,
        customerId,
        testDate: new Date(Date.now() - 550 * 24 * 60 * 60 * 1000).toISOString(),
        rightEyeSph: 1.25,
        rightEyeCyl: -0.25,
        rightEyeAxis: 92,
        leftEyeSph: 1.5,
        leftEyeCyl: -0.5,
        leftEyeAxis: 90,
        doctorName: 'Dr. Priya Sharma',
        renewalStatus: 'expired',
        daysUntilRenewal: -120,
      },
    ];
  };

  const generateMockInteractions = (): InteractionRecord[] => {
    const types: Array<'call' | 'sms' | 'email' | 'whatsapp' | 'in_person'> = ['call', 'sms', 'email', 'whatsapp', 'in_person'];
    const mockNotes = [
      'Inquiry about lens materials',
      'Purchase confirmation',
      'Prescription consultation',
      'Delivery tracking',
      'Warranty activation',
      'Follow-up on fitting',
    ];

    return Array.from({ length: 6 }, (_, i) => ({
      id: `interaction-${i}`,
      type: types[i % types.length],
      date: new Date(Date.now() - i * 7 * 24 * 60 * 60 * 1000).toISOString(),
      notes: mockNotes[i % mockNotes.length],
      duration: ['call'].includes(types[i % types.length]) ? Math.floor(Math.random() * 20) + 5 : undefined,
      initiatedBy: i % 3 === 0 ? 'Customer' : 'Business',
    }));
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  if (error || !customer) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center space-y-4">
          <AlertCircle className="w-12 h-12 text-red-500 mx-auto" />
          <p className="text-gray-400">{error || 'Customer not found'}</p>
          <button
            onClick={() => navigate('/customers')}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
          >
            Back to Customers
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <button
          onClick={() => navigate('/customers')}
          className="p-2 hover:bg-gray-800 rounded-lg transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <h1 className="text-3xl font-bold">Customer 360</h1>
      </div>

      {/* Customer Header Card */}
      <CustomerHeaderCard customer={customer} stats={stats} loyaltyData={loyaltyData} />

      {/* Tab Navigation */}
      <div className="flex gap-2 border-b border-gray-700 overflow-x-auto">
        {(['overview', 'prescriptions', 'orders', 'interactions', 'loyalty', 'preferences'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium whitespace-nowrap border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="bg-gray-800 rounded-lg p-6">
        {activeTab === 'overview' && stats && <OverviewTab stats={stats} />}
        {activeTab === 'prescriptions' && <PrescriptionsTab prescriptions={prescriptions} />}
        {activeTab === 'orders' && <OrdersTab orders={orders} />}
        {activeTab === 'interactions' && <InteractionsTab interactions={interactions} />}
        {activeTab === 'loyalty' && loyaltyData && <LoyaltyTab loyaltyData={loyaltyData} />}
        {activeTab === 'preferences' && <PreferencesTab />}
      </div>
    </div>
  );
}

// ============================================================================
// Customer Header Card Component
// ============================================================================

interface CustomerHeaderCardProps {
  customer: Customer;
  stats: CustomerStats | null;
  loyaltyData: LoyaltyData | null;
}

function CustomerHeaderCard({ customer, stats, loyaltyData }: CustomerHeaderCardProps) {
  const getTierColor = (tier: LoyaltyTier): string => {
    const colors = {
      Bronze: 'bg-amber-900 text-amber-300',
      Silver: 'bg-slate-700 text-slate-300',
      Gold: 'bg-yellow-700 text-yellow-300',
      Platinum: 'bg-blue-700 text-blue-300',
      Diamond: 'bg-purple-700 text-purple-300',
    };
    return colors[tier];
  };

  return (
    <div className="bg-gradient-to-r from-gray-800 to-gray-700 rounded-lg p-6 space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          {/* Avatar */}
          <div className="w-20 h-20 rounded-full bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center">
            <User className="w-10 h-10 text-white" />
          </div>

          {/* Customer Info */}
          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-white">{customer.name}</h2>
            <div className="flex items-center gap-4 text-gray-400">
              <div className="flex items-center gap-1">
                <Phone className="w-4 h-4" />
                <span>{customer.phone}</span>
              </div>
              <div className="flex items-center gap-1">
                <Mail className="w-4 h-4" />
                <span>{customer.email}</span>
              </div>
            </div>
            {customer.address && (
              <div className="flex items-center gap-1 text-gray-400">
                <MapPin className="w-4 h-4" />
                <span>{customer.address}</span>
              </div>
            )}
          </div>
        </div>

        {/* Loyalty Tier Badge */}
        {loyaltyData && (
          <div className={clsx('px-4 py-2 rounded-full font-semibold flex items-center gap-2', getTierColor(loyaltyData.tier))}>
            <Award className="w-5 h-5" />
            {loyaltyData.tier}
          </div>
        )}
      </div>

      {/* Key Metrics */}
      {stats && (
        <div className="grid grid-cols-5 gap-4 pt-4 border-t border-gray-700">
          <div className="text-center">
            <p className="text-gray-400 text-sm">Customer Since</p>
            <p className="text-white font-semibold">{new Date(stats.customerSinceDate).toLocaleDateString()}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-400 text-sm">Lifetime Value</p>
            <p className="text-green-400 font-semibold">₹{stats.totalLifetimeValue.toLocaleString('en-IN')}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-400 text-sm">Visit Frequency</p>
            <p className="text-white font-semibold">{stats.visitFrequency}/month</p>
          </div>
          <div className="text-center">
            <p className="text-gray-400 text-sm">Avg Order Value</p>
            <p className="text-white font-semibold">₹{stats.averageOrderValue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-400 text-sm">Last Order</p>
            <p className="text-white font-semibold">
              {stats.lastOrderDate ? new Date(stats.lastOrderDate).toLocaleDateString() : 'N/A'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Tab Components
// ============================================================================

interface OverviewTabProps {
  stats: CustomerStats;
}

function OverviewTab({ stats }: OverviewTabProps) {
  return (
    <div className="grid grid-cols-2 gap-6">
      {/* Left Column */}
      <div className="space-y-4">
        {/* Metrics Grid */}
        <div className="space-y-3">
          <MetricRow label="Total Orders" value={stats.totalOrders.toString()} icon={<ShoppingCart className="w-5 h-5" />} />
          <MetricRow label="Preferred Store" value={stats.preferredStore} icon={<MapPin className="w-5 h-5" />} />
          <MetricRow label="Visit Frequency" value={`${stats.visitFrequency} per month`} icon={<Calendar className="w-5 h-5" />} />
          <MetricRow label="Referrals Made" value={stats.referralCount.toString()} icon={<TrendingUp className="w-5 h-5" />} />
        </div>
      </div>

      {/* Right Column */}
      <div className="space-y-4">
        {/* Last Order */}
        <div className="bg-gray-700 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-white mb-3">Recent Activity</h3>
          <p className="text-gray-400 text-sm">
            {stats.lastOrderDate
              ? `Last purchase on ${new Date(stats.lastOrderDate).toLocaleDateString()} for ₹${stats.lastOrderAmount?.toLocaleString('en-IN')}`
              : 'No purchase history'}
          </p>
        </div>

        {/* Member Duration */}
        <div className="bg-gray-700 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-white mb-3">Quick Stats</h3>
          <p className="text-gray-400 text-sm">
            <strong className="text-white">Member Duration:</strong>{' '}
            {Math.floor(monthsAsSinceDate(stats.customerSinceDate) / 12)} years{' '}
            {(monthsAsSinceDate(stats.customerSinceDate) % 12)} months
          </p>
        </div>
      </div>
    </div>
  );
}

const monthsAsSinceDate = (dateString: string): number => {
  const date = new Date(dateString);
  const now = new Date();
  return (now.getFullYear() - date.getFullYear()) * 12 + (now.getMonth() - date.getMonth());
};

interface MetricRowProps {
  label: string;
  value: string;
  icon: React.ReactNode;
}

function MetricRow({ label, value, icon }: MetricRowProps) {
  return (
    <div className="flex items-center justify-between bg-gray-700 rounded-lg p-3">
      <div className="flex items-center gap-3">
        <div className="text-gray-400">{icon}</div>
        <span className="text-gray-300">{label}</span>
      </div>
      <span className="text-white font-semibold">{value}</span>
    </div>
  );
}

interface PrescriptionsTabProps {
  prescriptions: PrescriptionData[];
}

function PrescriptionsTab({ prescriptions }: PrescriptionsTabProps) {
  if (prescriptions.length === 0) {
    return <p className="text-gray-400">No prescriptions found</p>;
  }

  return (
    <div className="space-y-4">
      {prescriptions.map((rx) => (
        <div key={rx.id} className="bg-gray-700 rounded-lg p-4 space-y-2">
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Eye className="w-5 h-5 text-blue-400" />
                <span className="font-semibold text-white">Prescription #{rx.id?.slice(-6) || 'N/A'}</span>
              </div>
              <p className="text-gray-400 text-sm">Date: {new Date(rx.testDate).toLocaleDateString()}</p>
              {rx.doctorName && <p className="text-gray-400 text-sm">Doctor: {rx.doctorName}</p>}
            </div>

            {/* Renewal Status Badge */}
            <div className="flex items-center gap-2">
              <div
                className={clsx(
                  'px-3 py-1 rounded-full text-xs font-semibold flex items-center gap-1',
                  rx.renewalStatus === 'current'
                    ? 'bg-green-900 text-green-300'
                    : rx.renewalStatus === 'upcoming'
                      ? 'bg-yellow-900 text-yellow-300'
                      : 'bg-red-900 text-red-300'
                )}
              >
                {rx.renewalStatus === 'current' && <CheckCircle className="w-4 h-4" />}
                {rx.renewalStatus === 'upcoming' && <AlertTriangle className="w-4 h-4" />}
                {rx.renewalStatus === 'expired' && <AlertCircle className="w-4 h-4" />}
                {rx.renewalStatus.charAt(0).toUpperCase() + rx.renewalStatus.slice(1)}
              </div>
            </div>
          </div>

          {/* Prescription Details */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-gray-400">Right Eye (OD)</p>
              <p className="text-white">
                SPH: {rx.rightEyeSph || '-'} | CYL: {rx.rightEyeCyl || '-'} | AXIS: {rx.rightEyeAxis || '-'}
              </p>
            </div>
            <div>
              <p className="text-gray-400">Left Eye (OS)</p>
              <p className="text-white">
                SPH: {rx.leftEyeSph || '-'} | CYL: {rx.leftEyeCyl || '-'} | AXIS: {rx.leftEyeAxis || '-'}
              </p>
            </div>
          </div>

          {rx.daysUntilRenewal !== undefined && rx.renewalStatus !== 'current' && (
            <p className="text-xs text-gray-400 pt-2 border-t border-gray-600">
              {rx.renewalStatus === 'upcoming' ? `Renews in ${rx.daysUntilRenewal} days` : `Expired ${Math.abs(rx.daysUntilRenewal)} days ago`}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

interface OrdersTabProps {
  orders: any[];
}

function OrdersTab({ orders }: OrdersTabProps) {
  if (orders.length === 0) {
    return <p className="text-gray-400">No orders found</p>;
  }

  return (
    <div className="space-y-4">
      {orders.map((order) => (
        <div key={order.id} className="bg-gray-700 rounded-lg p-4">
          <div className="flex items-start justify-between mb-2">
            <div>
              <p className="font-semibold text-white">Order #{order.order_number || order.id?.slice(-6)}</p>
              <p className="text-gray-400 text-sm">{new Date(order.order_date || '').toLocaleDateString()}</p>
            </div>
            <div className="text-right">
              <p className="text-green-400 font-semibold">₹{(order.total_amount || 0).toLocaleString('en-IN')}</p>
              <p className="text-gray-400 text-xs capitalize">{order.status || 'Completed'}</p>
            </div>
          </div>
          <p className="text-gray-400 text-sm">{order.items?.length || 0} items</p>
        </div>
      ))}
    </div>
  );
}

interface InteractionsTabProps {
  interactions: InteractionRecord[];
}

function InteractionsTab({ interactions }: InteractionsTabProps) {
  const getInteractionIcon = (type: string) => {
    switch (type) {
      case 'call':
        return <PhoneCall className="w-5 h-5" />;
      case 'sms':
        return <MessageCircleIcon className="w-5 h-5" />;
      case 'email':
        return <Mail className="w-5 h-5" />;
      case 'whatsapp':
        return <MessageCircleIcon className="w-5 h-5" />;
      case 'in_person':
        return <User className="w-5 h-5" />;
      default:
        return <Clock className="w-5 h-5" />;
    }
  };

  return (
    <div className="space-y-4">
      {interactions.map((interaction) => (
        <div key={interaction.id} className="bg-gray-700 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <div className="text-gray-400 mt-1">{getInteractionIcon(interaction.type)}</div>
            <div className="flex-1">
              <div className="flex items-start justify-between">
                <div>
                  <p className="font-semibold text-white capitalize">{interaction.type.replace('_', ' ')}</p>
                  <p className="text-gray-400 text-sm">{interaction.notes}</p>
                </div>
                <div className="text-right">
                  <p className="text-gray-400 text-xs">{new Date(interaction.date).toLocaleDateString()}</p>
                  {interaction.duration && <p className="text-gray-400 text-xs">{interaction.duration} min</p>}
                </div>
              </div>
              <p className="text-gray-500 text-xs mt-2">Initiated by: {interaction.initiatedBy}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

interface LoyaltyTabProps {
  loyaltyData: LoyaltyData;
}

function LoyaltyTab({ loyaltyData }: LoyaltyTabProps) {
  const getTierThreshold = (tier: LoyaltyTier): number => {
    const thresholds = {
      Bronze: 0,
      Silver: 10000,
      Gold: 25000,
      Platinum: 50000,
      Diamond: 100000,
    };
    return thresholds[tier];
  };

  const nextTierThreshold = loyaltyData.tier === 'Diamond' ? 100000 : getTierThreshold(loyaltyData.tier) * 2.5;
  const progress = ((loyaltyData.points - getTierThreshold(loyaltyData.tier)) / (nextTierThreshold - getTierThreshold(loyaltyData.tier))) * 100;

  return (
    <div className="space-y-6">
      {/* Current Tier */}
      <div className="bg-gray-700 rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white">Current Tier</h3>
          <Award className="w-6 h-6 text-yellow-400" />
        </div>
        <p className="text-4xl font-bold text-yellow-400">{loyaltyData.tier}</p>
        <p className="text-gray-400">Member since {new Date(loyaltyData.memberSince).toLocaleDateString()}</p>
      </div>

      {/* Points Progress */}
      <div className="bg-gray-700 rounded-lg p-6 space-y-4">
        <h3 className="text-lg font-semibold text-white">Points Progress</h3>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Current Points</span>
            <span className="text-white font-semibold">{loyaltyData.points}</span>
          </div>
          <div className="w-full bg-gray-800 rounded-full h-3 overflow-hidden">
            <div className="bg-gradient-to-r from-blue-500 to-purple-500 h-full" style={{ width: `${Math.min(progress, 100)}%` }} />
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-400">
              {getTierThreshold(loyaltyData.tier).toLocaleString('en-IN')}
            </span>
            <span className="text-gray-400">
              {nextTierThreshold.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </span>
          </div>
        </div>
        <p className="text-gray-400 text-sm pt-2 border-t border-gray-600">
          {loyaltyData.pointsToNextTier} points needed to reach next tier
        </p>
      </div>

      {/* Loyalty Stats */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-700 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-2">Redeemed Points</p>
          <p className="text-2xl font-bold text-white">{loyaltyData.redeemedPoints}</p>
        </div>
        <div className="bg-gray-700 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-2">Total Earned</p>
          <p className="text-2xl font-bold text-green-400">{loyaltyData.totalPointsEarned}</p>
        </div>
      </div>
    </div>
  );
}

function PreferencesTab() {
  return (
    <div className="space-y-4">
      <div className="bg-gray-700 rounded-lg p-4">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Settings className="w-5 h-5" />
          Customer Preferences
        </h3>

        <div className="space-y-3">
          <PreferenceRow label="Newsletter" status={true} />
          <PreferenceRow label="SMS Updates" status={true} />
          <PreferenceRow label="Email Promotions" status={false} />
          <PreferenceRow label="Birthday Offers" status={true} />
          <PreferenceRow label="WhatsApp Communication" status={true} />
        </div>
      </div>

      <div className="bg-gray-700 rounded-lg p-4">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Gift className="w-5 h-5" />
          Product Interests
        </h3>
        <div className="flex flex-wrap gap-2">
          {['Spectacles', 'Sunglasses', 'Contact Lenses', 'Lens Coatings'].map((interest) => (
            <span key={interest} className="px-3 py-1 bg-blue-900 text-blue-300 rounded-full text-sm">
              {interest}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

interface PreferenceRowProps {
  label: string;
  status: boolean;
}

function PreferenceRow({ label, status }: PreferenceRowProps) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-300">{label}</span>
      <div className={clsx('w-10 h-6 rounded-full flex items-center px-1 cursor-pointer', status ? 'bg-green-600' : 'bg-gray-600')}>
        <div className={clsx('w-4 h-4 rounded-full bg-white transition-transform', status ? 'translate-x-4' : '')} />
      </div>
    </div>
  );
}

export default Customer360Dashboard;

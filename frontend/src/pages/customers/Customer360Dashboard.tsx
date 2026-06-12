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
  Search,
  Calendar,
  TrendingUp,
  Gift,
  Clock,
  CheckCircle,
  AlertTriangle,
  MessageCircle as MessageCircleIcon,
  PhoneCall,
  Layers,
} from 'lucide-react';
import type { Customer } from '../../types';
import { customerApi, orderApi, prescriptionApi } from '../../services/api';
import { loyaltyApi } from '../../services/api/loyalty';
import { marketingApi } from '../../services/api/marketing';
import StoreCreditLedgerCard from '../../components/customers/StoreCreditLedgerCard';
import { VipInterveneModal } from '../../components/customers/VipInterveneModal';
import { CustomerTagsPanel } from '../../components/customers/CustomerTagsPanel';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { PrescriptionVersionsEditor } from '../../components/clinical/PrescriptionVersionsEditor';
import { PrescriptionHistoryModal } from '../../components/clinical/PrescriptionHistoryModal';
import clsx from 'clsx';

type Customer360Tab = 'overview' | 'prescriptions' | 'orders' | 'interactions' | 'loyalty' | 'preferences';

type LoyaltyTier = 'Bronze' | 'Silver' | 'Gold' | 'Platinum';

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
  const { hasRole } = useAuth();
  // F40: only SUPERADMIN/ADMIN can launch a VIP intervention.
  const canIntervene = hasRole(['SUPERADMIN', 'ADMIN']);
  const [vipInterveneOpen, setVipInterveneOpen] = useState(false);

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [prescriptions, setPrescriptions] = useState<PrescriptionData[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [interactions, setInteractions] = useState<InteractionRecord[]>([]);
  const [stats, setStats] = useState<CustomerStats | null>(null);
  const [loyaltyData, setLoyaltyData] = useState<LoyaltyData | null>(null);
  const [activeTab, setActiveTab] = useState<Customer360Tab>('overview');
  // CRM-9: last NPS score for this customer (from nps_responses via nps-dashboard)
  const [lastNpsScore, setLastNpsScore] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Customer[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    if (!customerId) return;
    loadCustomerData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId]);

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const res = await customerApi.getCustomers({ search: searchQuery.trim(), limit: 10 });
      setSearchResults(res.customers || res.data || res || []);
    } catch {
      toast.error('Failed to search customers');
    } finally {
      setIsSearching(false);
    }
  };

  // Show search UI when no customerId
  if (!customerId) {
    return (
      <div className="min-h-screen bg-gray-50 p-6">
        <div className="max-w-2xl mx-auto">
          <h1 className="text-2xl font-bold text-gray-900 mb-2">Customer 360 View</h1>
          <p className="text-gray-600 mb-6">Search for a customer to view their complete profile</p>
          <div className="flex gap-2 mb-6">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <input
                type="text"
                placeholder="Search by name, phone, or email..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500"
              />
            </div>
            <button
              onClick={handleSearch}
              disabled={isSearching}
              className="px-4 py-2.5 bg-amber-600 text-white rounded-lg text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
            >
              {isSearching ? 'Searching...' : 'Search'}
            </button>
          </div>
          {searchResults.length > 0 && (
            <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
              {searchResults.map((c: any) => {
                const cid = c.customer_id || c._id || c.id;
                return (
                  <button
                    key={cid}
                    onClick={() => navigate(`/customers/${cid}/360`)}
                    className="w-full px-4 py-3 flex items-center gap-3 hover:bg-amber-50 transition-colors text-left"
                  >
                    <div className="w-9 h-9 bg-amber-100 rounded-full flex items-center justify-center">
                      <User className="w-4 h-4 text-amber-700" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">{c.name || `${c.first_name || ''} ${c.last_name || ''}`.trim()}</p>
                      <p className="text-xs text-gray-500">{c.mobile || c.phone || ''} {c.email ? ` · ${c.email}` : ''}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
          {searchResults.length === 0 && searchQuery && !isSearching && (
            <p className="text-center text-gray-500 text-sm mt-8">No customers found. Try a different search term.</p>
          )}
        </div>
      </div>
    );
  }

  const loadCustomerData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Fetch customer details
      const customerData = await customerApi.getCustomer(customerId!);
      setCustomer(customerData);

      // Fetch orders. getOrders returns an envelope {orders,total,data,...},
      // NOT a bare array — calling .reduce on it threw and broke the whole
      // page (blank total spend / "failed to load"). Orders come back
      // camelCase (grandTotal/createdAt), so read those with snake fallbacks.
      const ordersResp = await orderApi.getOrders({ customerId });
      const ordersData: any[] = Array.isArray(ordersResp)
        ? ordersResp
        : ordersResp?.orders || ordersResp?.data || [];
      setOrders(ordersData);

      const orderAmount = (o: any) => o?.grandTotal ?? o?.total_amount ?? 0;
      const orderDate = (o: any) => o?.createdAt ?? o?.order_date ?? o?.created_at ?? null;

      // Calculate stats
      const totalLTV = ordersData.reduce((sum: number, order: any) => sum + orderAmount(order), 0);
      const calculatedStats: CustomerStats = {
        totalLifetimeValue: totalLTV,
        totalOrders: ordersData.length,
        lastOrderDate: ordersData[0] ? orderDate(ordersData[0]) : null,
        lastOrderAmount: ordersData[0] ? orderAmount(ordersData[0]) : null,
        customerSinceDate: customerData.created_at,
        preferredStore: customerData.store_id || 'Main Store',
        averageOrderValue: (ordersData?.length || 0) > 0 ? totalLTV / (ordersData?.length || 1) : 0,
        visitFrequency: Math.round((ordersData?.length || 0) / Math.max(1, monthsAsSinceDate(customerData.created_at))),
        referralCount: 0,
        activeLoans: 0,
      };
      setStats(calculatedStats);

      // Set loyalty data from the real loyalty engine (not fabricated from
      // rupee totals). getAccount returns the canonical balance/tier/earned/
      // redeemed + the engine's tier thresholds so "points to next tier" is
      // honest. On failure (not enrolled / no engine), leave loyaltyData null
      // so the UI shows a "not enrolled" empty state instead of inventing one.
      try {
        const loyaltyResp = await loyaltyApi.getAccount(customerId!);
        const acct = loyaltyResp.account;
        const tierLabel = titleCaseTier(acct.tier);
        const thresholds = loyaltyResp.settings?.tier_thresholds || {};
        setLoyaltyData({
          tier: tierLabel,
          points: acct.balance_points ?? 0,
          pointsToNextTier: pointsToNextTierFromSettings(acct.tier, acct.lifetime_earned ?? 0, thresholds),
          redeemedPoints: acct.lifetime_redeemed ?? 0,
          totalPointsEarned: acct.lifetime_earned ?? 0,
          memberSince: acct.created_at || customerData.created_at,
        });
      } catch {
        // Customer not enrolled or loyalty engine unavailable — empty state.
        setLoyaltyData(null);
      }

      // Fetch real prescription data for this customer
      try {
        const rxData = await prescriptionApi.getPrescriptions(customerId!);
        const rxList: any[] = Array.isArray(rxData?.prescriptions)
          ? rxData.prescriptions
          : Array.isArray(rxData)
            ? rxData
            : [];

        const now = Date.now();
        const mapped: PrescriptionData[] = rxList.map((rx: any) => {
          const testDate = rx.testDate || rx.test_date || rx.createdAt || rx.created_at || '';
          const validityMonths: number = rx.validityMonths ?? rx.validity_months ?? 12;
          const expiryMs = new Date(testDate).getTime() + validityMonths * 30 * 24 * 60 * 60 * 1000;
          const daysUntilRenewal = Math.round((expiryMs - now) / (24 * 60 * 60 * 1000));
          let renewalStatus: 'current' | 'upcoming' | 'expired';
          if (daysUntilRenewal < 0) renewalStatus = 'expired';
          else if (daysUntilRenewal <= 30) renewalStatus = 'upcoming';
          else renewalStatus = 'current';

          return {
            id: rx.id || rx._id || '',
            customerId: rx.customerId || rx.customer_id || customerId!,
            testDate,
            rightEyeSph: rx.rightEye?.sphere ?? rx.right_eye?.sphere,
            rightEyeCyl: rx.rightEye?.cylinder ?? rx.right_eye?.cylinder,
            rightEyeAxis: rx.rightEye?.axis ?? rx.right_eye?.axis,
            leftEyeSph: rx.leftEye?.sphere ?? rx.left_eye?.sphere,
            leftEyeCyl: rx.leftEye?.cylinder ?? rx.left_eye?.cylinder,
            leftEyeAxis: rx.leftEye?.axis ?? rx.left_eye?.axis,
            doctorName: rx.optometristName || rx.optometrist_name,
            renewalStatus,
            daysUntilRenewal,
          };
        });

        setPrescriptions(mapped);
      } catch {
        // Prescription fetch failed; show empty state rather than crashing
        setPrescriptions([]);
      }

      // No interaction log API exists yet; start with empty state
      setInteractions([]);

      // CRM-9: fetch the most recent NPS score for this customer (fail-soft)
      try {
        const npsData = await marketingApi.getNpsDashboard(customerData.store_id);
        const responses: any[] = npsData?.responses || [];
        const customerNps = responses.find(
          (r: any) => r.customer_id === customerId && r.score != null
        );
        setLastNpsScore(customerNps?.score ?? null);
      } catch {
        setLastNpsScore(null);
      }

      // Removed the noisy "Customer data loaded successfully" toast — it
      // fired on every Customer 360 page open and was useless feedback
      // (the data is already visible on the page). QA polish, 2026-05-27.
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

  // Map the engine's UPPER_SNAKE tier to the Title-case label this view uses.
  const titleCaseTier = (tier: string): LoyaltyTier => {
    const t = (tier || 'BRONZE').toUpperCase();
    if (t === 'PLATINUM') return 'Platinum';
    if (t === 'GOLD') return 'Gold';
    if (t === 'SILVER') return 'Silver';
    return 'Bronze';
  };

  // "Points to next tier" computed from the engine's own tier_thresholds
  // (lifetime-earned-points based), not invented rupee bands. Returns 0 at the
  // top tier or when thresholds are unavailable.
  const pointsToNextTierFromSettings = (
    tier: string,
    lifetimeEarned: number,
    thresholds: Record<string, number>,
  ): number => {
    const order = ['BRONZE', 'SILVER', 'GOLD', 'PLATINUM'];
    const idx = order.indexOf((tier || 'BRONZE').toUpperCase());
    const nextKey = idx >= 0 && idx < order.length - 1 ? order[idx + 1] : null;
    if (!nextKey || thresholds[nextKey] === undefined) return 0;
    return Math.max(0, thresholds[nextKey] - lifetimeEarned);
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
          <p className="text-gray-500">{error || 'Customer not found'}</p>
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
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/customers')}
            className="btn icon ghost sm"
            aria-label="Back to customers list"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <div className="eyebrow mb-1.5">Customer 360</div>
            <h1>{customer?.name || 'Customer profile'}</h1>
            <div className="hint">Full story: purchases, prescriptions, patients, loyalty, follow-ups, communication log.</div>
          </div>
        </div>
      </div>

      {/* Customer Header Card */}
      <CustomerHeaderCard customer={customer} stats={stats} loyaltyData={loyaltyData} />

      {/* F39: manager-approved tags (feed the NBA daily call list) */}
      {customerId && (
        <CustomerTagsPanel customerId={customerId} tags={(customer as any)?.tags || []} />
      )}

      {/* Tab Navigation */}
      <div className="flex gap-2 border-b border-gray-200 overflow-x-auto">
        {(['overview', 'prescriptions', 'orders', 'interactions', 'loyalty', 'preferences'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium whitespace-nowrap border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="bg-white border border-gray-200 rounded-lg p-6">
        {activeTab === 'overview' && stats && (
          <OverviewTab
            stats={stats}
            lastNpsScore={lastNpsScore}
            vipChurnRisk={customer?.vip_churn_risk}
            canIntervene={canIntervene}
            onIntervene={() => setVipInterveneOpen(true)}
          />
        )}
        {activeTab === 'prescriptions' && <PrescriptionsTab prescriptions={prescriptions} customerId={customerId!} customerName={customer?.name} />}
        {activeTab === 'orders' && <OrdersTab orders={orders} />}
        {activeTab === 'interactions' && <InteractionsTab interactions={interactions} />}
        {activeTab === 'loyalty' && (
          <div className="space-y-4">
            {loyaltyData ? (
              <LoyaltyTab loyaltyData={loyaltyData} />
            ) : (
              <div className="text-center py-12 text-gray-500">
                <Award className="w-12 h-12 mx-auto mb-3 opacity-40" />
                <p>Not enrolled in the loyalty program yet.</p>
                <p className="text-sm">Points are earned automatically on qualifying purchases.</p>
              </div>
            )}
            {customerId && <StoreCreditLedgerCard customerId={customerId} />}
          </div>
        )}
        {activeTab === 'preferences' && <PreferencesTab customer={customer} />}
      </div>

      {/* F40: VIP intervention dialog — only mounted/usable for SUPERADMIN/ADMIN. */}
      {canIntervene && customerId && (
        <VipInterveneModal
          customerId={customerId}
          customerName={customer?.name}
          isOpen={vipInterveneOpen}
          onClose={() => setVipInterveneOpen(false)}
          onSuccess={loadCustomerData}
        />
      )}
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
      Bronze: 'bg-amber-50 text-amber-700',
      Silver: 'bg-gray-100 text-slate-700',
      Gold: 'bg-yellow-50 text-yellow-700',
      Platinum: 'bg-blue-50 text-blue-700',
    };
    return colors[tier];
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          {/* Avatar */}
          <div className="w-20 h-20 rounded-full bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center">
            <User className="w-10 h-10 text-gray-900" />
          </div>

          {/* Customer Info */}
          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-gray-900">{customer.name}</h2>
            <div className="flex items-center gap-4 text-gray-500">
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
              <div className="flex items-center gap-1 text-gray-500">
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
        <div className="grid grid-cols-2 tablet:grid-cols-5 gap-4 pt-4 border-t border-gray-200">
          <div className="text-center">
            <p className="text-gray-500 text-sm">Customer Since</p>
            <p className="text-gray-900 font-semibold">{new Date(stats.customerSinceDate).toLocaleDateString()}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-500 text-sm">Lifetime Value</p>
            <p className="text-green-600 font-semibold">₹{stats.totalLifetimeValue.toLocaleString('en-IN')}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-500 text-sm">Visit Frequency</p>
            <p className="text-gray-900 font-semibold">{stats.visitFrequency}/month</p>
          </div>
          <div className="text-center">
            <p className="text-gray-500 text-sm">Avg Order Value</p>
            <p className="text-gray-900 font-semibold">₹{stats.averageOrderValue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
          </div>
          <div className="text-center">
            <p className="text-gray-500 text-sm">Last Order</p>
            <p className="text-gray-900 font-semibold">
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
  lastNpsScore: number | null;
  // F40: personalised VIP churn-risk subdoc (present only for qualifying VIPs).
  vipChurnRisk?: Customer['vip_churn_risk'];
  canIntervene: boolean;
  onIntervene: () => void;
}

// CRM-9: NPS score label helpers
function npsLabel(score: number): string {
  if (score >= 9) return 'Promoter';
  if (score >= 7) return 'Passive';
  return 'Detractor';
}
function npsColor(score: number): string {
  if (score >= 9) return 'text-green-600';
  if (score >= 7) return 'text-blue-600';
  return 'text-orange-600';
}

function OverviewTab({ stats, lastNpsScore, vipChurnRisk, canIntervene, onIntervene }: OverviewTabProps) {
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
        {/* F40: VIP churn-risk card — personalised buying rhythm, shown only for
            qualifying VIPs (subdoc present). Replaces the flat recency note for
            this customer. Risk shown as coloured text only, no background fill. */}
        {vipChurnRisk && (
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-gray-700">VIP churn risk</h3>
              {vipChurnRisk.risk_label === 'HIGH' ? (
                <span className="text-sm font-medium text-red-600">HIGH</span>
              ) : vipChurnRisk.risk_label === 'WATCH' ? (
                <span className="text-sm font-medium text-amber-600">WATCH</span>
              ) : (
                <span className="text-sm text-gray-500">{vipChurnRisk.risk_label}</span>
              )}
            </div>
            <div className="text-sm text-gray-600 space-y-0.5">
              <p>Usual visit: every {vipChurnRisk.usual_interval_days} days</p>
              <p>Last visit: {vipChurnRisk.last_purchase_days_ago} days ago</p>
              <p>Overdue by: {vipChurnRisk.overdue_by_days} days</p>
            </div>
            {vipChurnRisk.narrative && (
              <p className="text-xs text-gray-500 italic mt-1">{vipChurnRisk.narrative}</p>
            )}
            {canIntervene && (
              <button
                type="button"
                onClick={onIntervene}
                className="mt-2 text-sm text-blue-600 underline hover:text-blue-800"
              >
                Intervene
              </button>
            )}
          </div>
        )}

        {/* Last Order */}
        <div className="bg-gray-50 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-gray-900 mb-3">Recent Activity</h3>
          <p className="text-gray-500 text-sm">
            {stats.lastOrderDate
              ? `Last purchase on ${new Date(stats.lastOrderDate).toLocaleDateString()} for ₹${stats.lastOrderAmount?.toLocaleString('en-IN')}`
              : 'No purchase history'}
          </p>
        </div>

        {/* Member Duration */}
        <div className="bg-gray-50 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-gray-900 mb-3">Quick Stats</h3>
          <p className="text-gray-500 text-sm">
            <strong className="text-gray-900">Member Duration:</strong>{' '}
            {Math.floor(monthsAsSinceDate(stats.customerSinceDate) / 12)} years{' '}
            {(monthsAsSinceDate(stats.customerSinceDate) % 12)} months
          </p>
        </div>

        {/* CRM-9: NPS score (auto-triggered on delivery, shown here) */}
        <div className="bg-gray-50 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">NPS Score</h3>
          {lastNpsScore !== null ? (
            <div className="flex items-baseline gap-2">
              <span className={clsx('text-2xl font-bold', npsColor(lastNpsScore))}>
                {lastNpsScore}/10
              </span>
              <span className={clsx('text-xs font-medium', npsColor(lastNpsScore))}>
                {npsLabel(lastNpsScore)}
              </span>
            </div>
          ) : (
            <p className="text-xs text-gray-400">No survey response yet</p>
          )}
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
    <div className="flex items-center justify-between bg-gray-50 rounded-lg p-3">
      <div className="flex items-center gap-3">
        <div className="text-gray-500">{icon}</div>
        <span className="text-gray-600">{label}</span>
      </div>
      <span className="text-gray-900 font-semibold">{value}</span>
    </div>
  );
}

interface PrescriptionsTabProps {
  prescriptions: PrescriptionData[];
  customerId: string;
  customerName?: string;
}

function PrescriptionsTab({ prescriptions, customerId, customerName }: PrescriptionsTabProps) {
  const [versionsRxId, setVersionsRxId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  if (prescriptions.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <Eye className="w-12 h-12 mx-auto mb-3 opacity-40" />
        <p>No prescriptions recorded.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {versionsRxId && (
        <PrescriptionVersionsEditor
          prescriptionId={versionsRxId}
          isOpen={!!versionsRxId}
          onClose={() => setVersionsRxId(null)}
        />
      )}
      <PrescriptionHistoryModal
        customerId={customerId}
        customerName={customerName}
        isOpen={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onOpenVersions={(rxId) => {
          setHistoryOpen(false);
          setVersionsRxId(rxId);
        }}
      />
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setHistoryOpen(true)}
          className="px-3 py-1.5 text-sm font-semibold text-gray-700 bg-white border border-gray-300 rounded hover:bg-gray-50 flex items-center gap-1.5"
        >
          <Clock className="w-4 h-4" />
          History &amp; progression
        </button>
      </div>
      {prescriptions.map((rx) => (
        <div key={rx.id} className="bg-gray-50 rounded-lg p-4 space-y-2">
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Eye className="w-5 h-5 text-blue-600" />
                <span className="font-semibold text-gray-900">Prescription #{rx.id?.slice(-6) || 'N/A'}</span>
              </div>
              <p className="text-gray-500 text-sm">Date: {new Date(rx.testDate).toLocaleDateString()}</p>
              {rx.doctorName && <p className="text-gray-500 text-sm">Doctor: {rx.doctorName}</p>}
            </div>

            {/* Renewal Status Badge */}
            <div className="flex items-center gap-2">
              <div
                className={clsx(
                  'px-3 py-1 rounded-full text-xs font-semibold flex items-center gap-1',
                  rx.renewalStatus === 'current'
                    ? 'bg-green-50 text-green-700'
                    : rx.renewalStatus === 'upcoming'
                      ? 'bg-yellow-50 text-yellow-700'
                      : 'bg-red-50 text-red-700'
                )}
              >
                {rx.renewalStatus === 'current' && <CheckCircle className="w-4 h-4" />}
                {rx.renewalStatus === 'upcoming' && <AlertTriangle className="w-4 h-4" />}
                {rx.renewalStatus === 'expired' && <AlertCircle className="w-4 h-4" />}
                {rx.renewalStatus.charAt(0).toUpperCase() + rx.renewalStatus.slice(1)}
              </div>
              {rx.id && (
                <button
                  type="button"
                  onClick={() => setVersionsRxId(rx.id)}
                  className="px-2 py-1 text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 rounded flex items-center gap-1"
                  title="Manage 4-version Rx"
                >
                  <Layers className="w-3.5 h-3.5" />
                  Versions
                </button>
              )}
            </div>
          </div>

          {/* Prescription Details */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-gray-500">Right Eye (OD)</p>
              <p className="text-gray-900">
                SPH: {rx.rightEyeSph || '-'} | CYL: {rx.rightEyeCyl || '-'} | AXIS: {rx.rightEyeAxis || '-'}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Left Eye (OS)</p>
              <p className="text-gray-900">
                SPH: {rx.leftEyeSph || '-'} | CYL: {rx.leftEyeCyl || '-'} | AXIS: {rx.leftEyeAxis || '-'}
              </p>
            </div>
          </div>

          {rx.daysUntilRenewal !== undefined && rx.renewalStatus !== 'current' && (
            <p className="text-xs text-gray-500 pt-2 border-t border-gray-200">
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
    return <p className="text-gray-500">No orders found</p>;
  }

  return (
    <div className="space-y-4">
      {orders.map((order) => (
        <div key={order.id} className="bg-gray-50 rounded-lg p-4">
          <div className="flex items-start justify-between mb-2">
            <div>
              <p className="font-semibold text-gray-900">Order #{order.orderNumber || order.order_number || order.id?.slice(-6)}</p>
              <p className="text-gray-500 text-sm">{new Date(order.createdAt || order.order_date || '').toLocaleDateString()}</p>
            </div>
            <div className="text-right">
              <p className="text-green-600 font-semibold">₹{(order.grandTotal ?? order.total_amount ?? 0).toLocaleString('en-IN')}</p>
              <p className="text-gray-500 text-xs capitalize">{order.status || 'Completed'}</p>
            </div>
          </div>
          <p className="text-gray-500 text-sm">{order.items?.length || 0} items</p>
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

  if (interactions.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <MessageCircleIcon className="w-12 h-12 mx-auto mb-3 opacity-40" />
        <p>No interactions recorded.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {interactions.map((interaction) => (
        <div key={interaction.id} className="bg-gray-50 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <div className="text-gray-500 mt-1">{getInteractionIcon(interaction.type)}</div>
            <div className="flex-1">
              <div className="flex items-start justify-between">
                <div>
                  <p className="font-semibold text-gray-900 capitalize">{interaction.type.replace('_', ' ')}</p>
                  <p className="text-gray-500 text-sm">{interaction.notes}</p>
                </div>
                <div className="text-right">
                  <p className="text-gray-500 text-xs">{new Date(interaction.date).toLocaleDateString()}</p>
                  {interaction.duration && <p className="text-gray-500 text-xs">{interaction.duration} min</p>}
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
  // Progress toward the next tier is driven by the engine-supplied
  // pointsToNextTier (lifetime-earned vs the next threshold), not invented
  // rupee bands. At the top tier pointsToNextTier is 0 -> bar full.
  const atTopTier = loyaltyData.tier === 'Platinum';
  const nextTierTotal = loyaltyData.totalPointsEarned + loyaltyData.pointsToNextTier;
  const progress = atTopTier
    ? 100
    : nextTierTotal > 0
      ? (loyaltyData.totalPointsEarned / nextTierTotal) * 100
      : 0;

  return (
    <div className="space-y-6">
      {/* Current Tier */}
      <div className="bg-gray-50 rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">Current Tier</h3>
          <Award className="w-6 h-6 text-yellow-600" />
        </div>
        <p className="text-4xl font-bold text-yellow-600">{loyaltyData.tier}</p>
        <p className="text-gray-500">Member since {new Date(loyaltyData.memberSince).toLocaleDateString()}</p>
      </div>

      {/* Points Progress */}
      <div className="bg-gray-50 rounded-lg p-6 space-y-4">
        <h3 className="text-lg font-semibold text-gray-900">Points Progress</h3>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-gray-500">Current Balance</span>
            <span className="text-gray-900 font-semibold">{loyaltyData.points.toLocaleString('en-IN')} pts</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
            <div className="bg-gradient-to-r from-blue-500 to-purple-500 h-full" style={{ width: `${Math.min(progress, 100)}%` }} />
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-500">
              {loyaltyData.totalPointsEarned.toLocaleString('en-IN')} earned
            </span>
            <span className="text-gray-500">
              {nextTierTotal.toLocaleString('en-IN')}
            </span>
          </div>
        </div>
        <p className="text-gray-500 text-sm pt-2 border-t border-gray-200">
          {atTopTier
            ? 'Top tier reached.'
            : `${loyaltyData.pointsToNextTier.toLocaleString('en-IN')} points needed to reach next tier`}
        </p>
      </div>

      {/* Loyalty Stats */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-50 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-2">Redeemed Points</p>
          <p className="text-2xl font-bold text-gray-900">{loyaltyData.redeemedPoints}</p>
        </div>
        <div className="bg-gray-50 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-2">Total Earned</p>
          <p className="text-2xl font-bold text-green-600">{loyaltyData.totalPointsEarned}</p>
        </div>
      </div>
    </div>
  );
}

function PreferencesTab({ customer }: { customer: Customer | null }) {
  // Only marketing consent is actually captured on the customer record today.
  // The previous version rendered fabricated toggles (Newsletter/SMS/etc.) and
  // a fake product-interest list — removed so this reflects real data only.
  // Marketing consent is now an editable toggle (was read-only). Missing/None
  // defaults to opted-in (matches the create-path default); only an explicit
  // false means opted out. Toggling PATCHes the customer record, and the
  // backend send path skips opted-out customers (consent/DLT compliance).
  const toast = useToast();
  const custId = (customer as any)?.customer_id || (customer as any)?.id || '';
  const [consent, setConsent] = useState<boolean>(((customer as any)?.marketing_consent) !== false);
  const [savingConsent, setSavingConsent] = useState(false);
  useEffect(() => {
    setConsent(((customer as any)?.marketing_consent) !== false);
  }, [customer]);

  const toggleConsent = async () => {
    if (!custId || savingConsent) return;
    const next = !consent;
    setConsent(next); // optimistic
    setSavingConsent(true);
    try {
      await customerApi.updateCustomer(custId, { marketing_consent: next });
      toast.success(next ? 'Marketing messages enabled' : 'Customer opted out of marketing messages');
    } catch {
      setConsent(!next); // revert on failure
      toast.error('Could not update marketing consent');
    } finally {
      setSavingConsent(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-gray-50 rounded-lg p-4">
        <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Settings className="w-5 h-5" />
          Communication Preferences
        </h3>
        <PreferenceRow
          label="Receive marketing messages"
          status={consent}
          onToggle={custId ? toggleConsent : undefined}
          saving={savingConsent}
        />
        <p className="text-xs text-gray-500 mt-2">
          Birthday wishes, Rx-renewal reminders, and offers via SMS / WhatsApp. Turning this off stops all promotional messages to this customer.
        </p>
      </div>

      <div className="bg-gray-50 rounded-lg p-4">
        <h3 className="text-lg font-semibold text-gray-900 mb-2 flex items-center gap-2">
          <Gift className="w-5 h-5" />
          Product Interests
        </h3>
        <p className="text-gray-500 text-sm">Product interests aren't tracked yet.</p>
      </div>
    </div>
  );
}

interface PreferenceRowProps {
  label: string;
  status: boolean;
  onToggle?: () => void;
  saving?: boolean;
}

function PreferenceRow({ label, status, onToggle, saving }: PreferenceRowProps) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-600">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={status ? "true" : "false"}
        aria-label={label}
        disabled={!onToggle || saving}
        onClick={onToggle}
        className={clsx(
          'w-10 h-6 rounded-full flex items-center px-1 transition-colors',
          onToggle ? 'cursor-pointer' : 'cursor-default',
          saving ? 'opacity-50' : '',
          status ? 'bg-green-600' : 'bg-gray-300',
        )}
      >
        <div className={clsx('w-4 h-4 rounded-full bg-white transition-transform', status ? 'translate-x-4' : '')} />
      </button>
    </div>
  );
}

export default Customer360Dashboard;

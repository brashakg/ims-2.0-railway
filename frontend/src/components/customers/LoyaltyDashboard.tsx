// ============================================================================
// IMS 2.0 - Customer Loyalty Dashboard
// ============================================================================
// Display loyalty points, tier benefits, and redemption options

import { useState, useEffect } from 'react';
import {
  Award,
  Gift,
  TrendingUp,
  Users,
  Star,
  Calendar,
  ArrowRight,
  Check,
  Copy,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  type LoyaltyTier,
  type CustomerLoyaltyProfile,
  type LoyaltyTransaction,
  LOYALTY_TIERS,
  calculatePointsDiscount,
  getNextTier,
} from '../../constants/loyalty';
import clsx from 'clsx';

interface LoyaltyDashboardProps {
  customerId: string;
  onRedeemPoints?: (points: number) => void;
}

export function LoyaltyDashboard({ customerId, onRedeemPoints }: LoyaltyDashboardProps) {
  const toast = useToast();

  const [profile, setProfile] = useState<CustomerLoyaltyProfile | null>(null);
  const [transactions, setTransactions] = useState<LoyaltyTransaction[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showRedeemModal, setShowRedeemModal] = useState(false);
  const [redeemPoints, setRedeemPoints] = useState('');
  const [copiedReferral, setCopiedReferral] = useState(false);

  useEffect(() => {
    loadLoyaltyData();
  }, [customerId]);

  const loadLoyaltyData = async () => {
    setIsLoading(true);
    try {
      // In production, fetch from API
      await new Promise(resolve => setTimeout(resolve, 1000));

      // Mock data
      const mockProfile: CustomerLoyaltyProfile = {
        customerId,
        totalPointsEarned: 8500,
        totalPointsRedeemed: 1200,
        currentBalance: 7300,
        tier: 'GOLD',
        tierStartDate: '2025-06-15',
        nextTierPoints: 7700, // Points needed for Platinum
        lifetimeValue: 85000,
        memberSince: '2024-03-10',
        lastActivityDate: '2026-02-01',
        referralCode: 'JOH1234',
        referralCount: 3,
        isActive: true,
      };

      const mockTransactions: LoyaltyTransaction[] = [
        {
          id: 'txn1',
          customerId,
          type: 'EARNED',
          points: 450,
          reason: 'Purchase at Store',
          orderId: 'ORD-2024-001',
          balanceBefore: 6850,
          balanceAfter: 7300,
          createdAt: '2026-02-01',
        },
        {
          id: 'txn2',
          customerId,
          type: 'REDEEMED',
          points: -500,
          reason: 'Redeemed for discount',
          orderId: 'ORD-2024-002',
          balanceBefore: 7350,
          balanceAfter: 6850,
          createdAt: '2026-01-28',
        },
        {
          id: 'txn3',
          customerId,
          type: 'EARNED',
          points: 100,
          reason: 'Birthday Bonus',
          balanceBefore: 7250,
          balanceAfter: 7350,
          createdAt: '2026-01-25',
        },
      ];

      setProfile(mockProfile);
      setTransactions(mockTransactions);
    } catch (error: any) {
      toast.error(error?.message || 'Failed to load loyalty data');
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopyReferralCode = () => {
    if (profile?.referralCode) {
      navigator.clipboard.writeText(profile.referralCode);
      setCopiedReferral(true);
      toast.success('Referral code copied!');
      setTimeout(() => setCopiedReferral(false), 2000);
    }
  };

  const handleRedeemSubmit = () => {
    const points = parseInt(redeemPoints);
    if (!points || points <= 0) {
      toast.error('Please enter a valid number of points');
      return;
    }

    if (!profile || points > profile.currentBalance) {
      toast.error('Insufficient points balance');
      return;
    }

    const discount = calculatePointsDiscount(points, profile.tier);
    if (onRedeemPoints) {
      onRedeemPoints(points);
      toast.success(`₹${discount} discount applied! ${points} points redeemed.`);
      setShowRedeemModal(false);
      setRedeemPoints('');
      loadLoyaltyData(); // Refresh data
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="card text-center py-12 text-gray-500">
        <Award className="w-12 h-12 mx-auto mb-2 opacity-50" />
        <p>Loyalty profile not found</p>
      </div>
    );
  }

  const tierConfig = LOYALTY_TIERS[profile.tier];
  const nextTierInfo = getNextTier(profile.tier, profile.currentBalance);
  const tierProgress = nextTierInfo
    ? ((profile.currentBalance - tierConfig.minPoints) /
        (LOYALTY_TIERS[nextTierInfo.nextTier!].minPoints - tierConfig.minPoints)) *
      100
    : 100;

  return (
    <div className="space-y-4">
      {/* Header with Refresh */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-900">Loyalty Rewards</h2>
        <button
          onClick={loadLoyaltyData}
          disabled={isLoading}
          className="btn-outline text-sm flex items-center gap-2"
        >
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Refresh
        </button>
      </div>

      {/* Tier Card */}
      <div
        className="card relative overflow-hidden"
        style={{ backgroundColor: `${tierConfig.color}20`, borderColor: tierConfig.color }}
      >
        <div className="absolute top-0 right-0 text-8xl opacity-10">{tierConfig.icon}</div>
        <div className="relative z-10">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-3xl">{tierConfig.icon}</span>
                <h3 className="text-2xl font-bold text-gray-900">{tierConfig.name} Member</h3>
              </div>
              <p className="text-sm text-gray-600">Member since {new Date(profile.memberSince).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' })}</p>
            </div>
            <div className="text-right">
              <p className="text-3xl font-bold text-gray-900">{profile.currentBalance.toLocaleString()}</p>
              <p className="text-sm text-gray-600">Available Points</p>
            </div>
          </div>

          {/* Progress to Next Tier */}
          {nextTierInfo && nextTierInfo.nextTier && (
            <div>
              <div className="flex items-center justify-between text-sm mb-2">
                <span className="text-gray-600">
                  {nextTierInfo.pointsNeeded} points to {LOYALTY_TIERS[nextTierInfo.nextTier].name}
                </span>
                <span className="font-medium text-gray-900">{Math.round(tierProgress)}%</span>
              </div>
              <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-pink-500 transition-all"
                  style={{ width: `${tierProgress}%` }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Points Summary */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card text-center">
          <TrendingUp className="w-8 h-8 mx-auto mb-2 text-green-600" />
          <p className="text-2xl font-bold text-gray-900">{profile.totalPointsEarned.toLocaleString()}</p>
          <p className="text-xs text-gray-600">Total Earned</p>
        </div>
        <div className="card text-center">
          <Gift className="w-8 h-8 mx-auto mb-2 text-purple-600" />
          <p className="text-2xl font-bold text-gray-900">{profile.totalPointsRedeemed.toLocaleString()}</p>
          <p className="text-xs text-gray-600">Total Redeemed</p>
        </div>
        <div className="card text-center">
          <Users className="w-8 h-8 mx-auto mb-2 text-blue-600" />
          <p className="text-2xl font-bold text-gray-900">{profile.referralCount}</p>
          <p className="text-xs text-gray-600">Referrals</p>
        </div>
        <div className="card text-center">
          <Award className="w-8 h-8 mx-auto mb-2 text-orange-600" />
          <p className="text-2xl font-bold text-gray-900">₹{profile.lifetimeValue.toLocaleString()}</p>
          <p className="text-xs text-gray-600">Lifetime Value</p>
        </div>
      </div>

      {/* Actions */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        {/* Redeem Points */}
        <div className="card">
          <h3 className="font-semibold text-gray-900 mb-2 flex items-center gap-2">
            <Gift className="w-5 h-5 text-purple-600" />
            Redeem Points
          </h3>
          <p className="text-sm text-gray-600 mb-4">
            Redeem {tierConfig.redeemRate} points for ₹1 discount on your next purchase
          </p>
          <button
            onClick={() => setShowRedeemModal(true)}
            className="btn-primary w-full flex items-center justify-center gap-2"
          >
            <Gift className="w-4 h-4" />
            Redeem Now
          </button>
        </div>

        {/* Referral Code */}
        <div className="card">
          <h3 className="font-semibold text-gray-900 mb-2 flex items-center gap-2">
            <Users className="w-5 h-5 text-blue-600" />
            Refer a Friend
          </h3>
          <p className="text-sm text-gray-600 mb-4">
            Share your code and earn 500 points when they make their first purchase
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={profile.referralCode}
              readOnly
              className="input-field flex-1 font-mono font-bold text-center"
            />
            <button
              onClick={handleCopyReferralCode}
              className="btn-outline flex items-center gap-2"
            >
              {copiedReferral ? (
                <Check className="w-4 h-4 text-green-600" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Tier Benefits */}
      <div className="card">
        <h3 className="font-semibold text-gray-900 mb-3 flex items-center gap-2">
          <Star className="w-5 h-5 text-yellow-600" />
          Your {tierConfig.name} Benefits
        </h3>
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
          {tierConfig.benefits.map((benefit, idx) => (
            <div key={idx} className="flex items-start gap-2 text-sm">
              <Check className="w-4 h-4 text-green-600 flex-shrink-0 mt-0.5" />
              <span className="text-gray-700">{benefit}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Transactions */}
      <div className="card">
        <h3 className="font-semibold text-gray-900 mb-3 flex items-center gap-2">
          <Calendar className="w-5 h-5 text-gray-600" />
          Recent Activity
        </h3>
        <div className="space-y-2">
          {transactions.map((txn) => (
            <div key={txn.id} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
              <div className="flex items-center gap-3">
                <div className={clsx(
                  'w-8 h-8 rounded-full flex items-center justify-center',
                  txn.type === 'EARNED' ? 'bg-green-100' : 'bg-red-100'
                )}>
                  {txn.type === 'EARNED' ? (
                    <TrendingUp className="w-4 h-4 text-green-600" />
                  ) : (
                    <Gift className="w-4 h-4 text-red-600" />
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-900">{txn.reason}</p>
                  <p className="text-xs text-gray-500">{new Date(txn.createdAt).toLocaleDateString('en-IN')}</p>
                </div>
              </div>
              <span className={clsx(
                'text-sm font-bold',
                txn.type === 'EARNED' ? 'text-green-600' : 'text-red-600'
              )}>
                {txn.type === 'EARNED' ? '+' : ''}{txn.points}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Redeem Modal */}
      {showRedeemModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <h3 className="text-xl font-bold text-gray-900 mb-4">Redeem Points</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Points to Redeem
                </label>
                <input
                  type="number"
                  value={redeemPoints}
                  onChange={(e) => setRedeemPoints(e.target.value)}
                  className="input-field w-full"
                  placeholder="Enter points"
                  min="0"
                  max={profile.currentBalance}
                />
                <p className="text-xs text-gray-500 mt-1">
                  Available: {profile.currentBalance} points
                </p>
              </div>

              {redeemPoints && parseInt(redeemPoints) > 0 && (
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-3">
                  <p className="text-sm text-gray-700">
                    You will get{' '}
                    <span className="font-bold text-purple-700">
                      ₹{calculatePointsDiscount(parseInt(redeemPoints), profile.tier)}
                    </span>{' '}
                    discount on your next purchase
                  </p>
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setShowRedeemModal(false);
                    setRedeemPoints('');
                  }}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  onClick={handleRedeemSubmit}
                  className="btn-primary flex-1 flex items-center justify-center gap-2"
                >
                  <Gift className="w-4 h-4" />
                  Redeem
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default LoyaltyDashboard;

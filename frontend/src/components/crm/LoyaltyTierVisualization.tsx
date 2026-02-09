// ============================================================================
// IMS 2.0 - Loyalty Tier Visualization
// ============================================================================
// Beautiful tier progression with milestone tracking and benefits

import { Trophy, Gift, Star, Zap, TrendingUp } from 'lucide-react';
import clsx from 'clsx';

export type LoyaltyTier = 'Bronze' | 'Silver' | 'Gold' | 'Platinum' | 'Diamond';

interface LoyaltyTierVisualizationProps {
  currentTier: LoyaltyTier;
  currentValue: number;
  pointsToNextTier: number;
  totalPointsEarned: number;
  memberSince: string;
}

export function LoyaltyTierVisualization({
  currentTier,
  currentValue,
  pointsToNextTier,
  totalPointsEarned,
  memberSince,
}: LoyaltyTierVisualizationProps) {
  const tiers: LoyaltyTier[] = ['Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond'];
  const tierThresholds = {
    Bronze: 0,
    Silver: 10000,
    Gold: 25000,
    Platinum: 50000,
    Diamond: 100000,
  };

  const tierBenefits = {
    Bronze: ['Basic member', '1x points on all purchases', 'Birthday bonus'],
    Silver: ['5% discount', '1.25x points multiplier', 'Free shipping on orders >₹500'],
    Gold: ['10% discount', '1.5x points multiplier', 'Priority customer service', 'Free eye test annually'],
    Platinum: ['15% discount', '2x points multiplier', 'Complimentary lens upgrade', 'VIP customer service'],
    Diamond: ['20% discount', '3x points multiplier', 'Personal account manager', 'Exclusive product previews'],
  };

  const tierColors = {
    Bronze: { bg: 'bg-amber-50', border: 'border-amber-300', badge: 'bg-amber-100 text-amber-800' },
    Silver: { bg: 'bg-slate-50', border: 'border-slate-300', badge: 'bg-slate-100 text-slate-800' },
    Gold: { bg: 'bg-yellow-50', border: 'border-yellow-300', badge: 'bg-yellow-100 text-yellow-800' },
    Platinum: { bg: 'bg-blue-50', border: 'border-blue-300', badge: 'bg-blue-100 text-blue-800' },
    Diamond: { bg: 'bg-purple-50', border: 'border-purple-300', badge: 'bg-purple-100 text-purple-800' },
  };

  const getTierIcon = (tier: LoyaltyTier) => {
    switch (tier) {
      case 'Bronze':
        return <Star className="w-6 h-6" />;
      case 'Silver':
        return <Trophy className="w-6 h-6" />;
      case 'Gold':
        return <Trophy className="w-6 h-6" />;
      case 'Platinum':
        return <Trophy className="w-6 h-6" />;
      case 'Diamond':
        return <Zap className="w-6 h-6" />;
    }
  };

  const currentTierIndex = tiers.indexOf(currentTier);
  const progressToNextTier = currentTier === 'Diamond' ? 100 : ((currentValue - tierThresholds[currentTier]) / (tierThresholds[tiers[currentTierIndex + 1]] - tierThresholds[currentTier])) * 100;

  const membershipMonths = (() => {
    const date = new Date(memberSince);
    const now = new Date();
    return (now.getFullYear() - date.getFullYear()) * 12 + (now.getMonth() - date.getMonth());
  })();

  return (
    <div className="space-y-6">
      {/* Current Tier Card */}
      <div className={clsx('rounded-lg border-2 p-6 space-y-4', tierColors[currentTier].border, tierColors[currentTier].bg)}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="text-4xl">{getTierIcon(currentTier)}</div>
            <div>
              <p className="text-sm text-gray-600">Current Tier</p>
              <p className="text-3xl font-bold text-gray-900">{currentTier}</p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-sm text-gray-600">Member for</p>
            <p className="text-2xl font-bold text-gray-900">{membershipMonths} months</p>
          </div>
        </div>

        {/* Progress to Next Tier */}
        {currentTier !== 'Diamond' && (
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-gray-700">Progress to {tiers[currentTierIndex + 1]}</span>
              <span className="font-medium text-gray-900">₹{currentValue.toLocaleString('en-IN')} / ₹{tierThresholds[tiers[currentTierIndex + 1]].toLocaleString('en-IN')}</span>
            </div>
            <div className="w-full bg-gray-300 rounded-full h-3 overflow-hidden">
              <div
                className={clsx(
                  'h-full rounded-full transition-all duration-300',
                  currentTier === 'Bronze' && 'bg-amber-500',
                  currentTier === 'Silver' && 'bg-slate-500',
                  currentTier === 'Gold' && 'bg-yellow-500',
                  currentTier === 'Platinum' && 'bg-blue-500',
                )}
                style={{ width: `${Math.min(progressToNextTier, 100)}%` }}
              />
            </div>
            <p className="text-xs text-gray-600 text-right">
              ₹{pointsToNextTier.toLocaleString('en-IN')} to go
            </p>
          </div>
        )}

        {currentTier === 'Diamond' && (
          <div className="bg-purple-100 border border-purple-300 rounded-lg p-3 flex items-center gap-2">
            <Zap className="w-5 h-5 text-purple-600 flex-shrink-0" />
            <p className="text-sm text-purple-800">
              🎉 <strong>Congratulations!</strong> You've reached our highest loyalty tier!
            </p>
          </div>
        )}
      </div>

      {/* Tier Progression */}
      <div className="space-y-3">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <TrendingUp className="w-5 h-5" />
          Tier Progression
        </h3>
        <div className="flex gap-2 overflow-x-auto pb-2">
          {tiers.map((tier, index) => (
            <div key={tier} className="flex flex-col items-center gap-1 flex-shrink-0">
              <button
                className={clsx(
                  'w-16 h-16 rounded-full flex items-center justify-center font-bold text-xl transition-all',
                  index <= currentTierIndex
                    ? 'bg-gradient-to-br from-yellow-400 to-orange-500 text-white shadow-lg scale-110'
                    : 'bg-gray-200 text-gray-600'
                )}
              >
                {tier === 'Bronze' && '🥉'}
                {tier === 'Silver' && '🥈'}
                {tier === 'Gold' && '🥇'}
                {tier === 'Platinum' && '💎'}
                {tier === 'Diamond' && '✨'}
              </button>
              <p className="text-xs font-medium text-gray-700">{tier}</p>
              <p className="text-xs text-gray-500">₹{tierThresholds[tier].toLocaleString('en-IN')}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Current Tier Benefits */}
      <div className={clsx('rounded-lg border-2 p-6 space-y-4', tierColors[currentTier].border)}>
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <Gift className="w-5 h-5" />
          Your {currentTier} Tier Benefits
        </h3>
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
          {tierBenefits[currentTier].map((benefit, index) => (
            <div key={index} className="flex items-start gap-3">
              <Star className="w-4 h-4 text-yellow-500 flex-shrink-0 mt-1" />
              <p className="text-sm text-gray-700">{benefit}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Points Summary */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-blue-700 uppercase tracking-wider">Total Points Earned</p>
          <p className="text-3xl font-bold text-blue-900 mt-2">{totalPointsEarned.toLocaleString('en-IN')}</p>
          <p className="text-xs text-blue-600 mt-1">Lifetime loyalty points</p>
        </div>
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-green-700 uppercase tracking-wider">Current Value</p>
          <p className="text-3xl font-bold text-green-900 mt-2">₹{currentValue.toLocaleString('en-IN')}</p>
          <p className="text-xs text-green-600 mt-1">Lifetime purchases</p>
        </div>
      </div>

      {/* Upgrade Path */}
      {currentTier !== 'Diamond' && (
        <div className="bg-gradient-to-r from-blue-50 to-purple-50 border border-blue-200 rounded-lg p-4 space-y-3">
          <h4 className="font-semibold text-gray-900">How to reach {tiers[currentTierIndex + 1]}</h4>
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-sm font-bold">
                1
              </div>
              <p className="text-sm text-gray-700">
                Spend <strong>₹{(tierThresholds[tiers[currentTierIndex + 1]] - currentValue).toLocaleString('en-IN')}</strong> more
              </p>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-sm font-bold">
                2
              </div>
              <p className="text-sm text-gray-700">You'll automatically reach {tiers[currentTierIndex + 1]} tier</p>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-sm font-bold">
                3
              </div>
              <p className="text-sm text-gray-700">Enjoy exclusive {tiers[currentTierIndex + 1].toLowerCase()} member benefits!</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default LoyaltyTierVisualization;

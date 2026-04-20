// ============================================================================
// IMS 2.0 - Loyalty Program Management
// ============================================================================
// 5-tier loyalty system: Bronze/Silver/Gold/Platinum/Diamond

import { useState } from 'react';
import { Gift, Edit, Plus, Settings } from 'lucide-react';
import clsx from 'clsx';

interface LoyaltyTier {
  name: string;
  minValue: number;
  maxValue: number;
  color: string;
  bgColor: string;
  badge: string;
  benefits: string[];
  pointsMultiplier: number;
  customerCount: number;
  totalMembers: number;
}

const LOYALTY_TIERS: LoyaltyTier[] = [
  {
    name: 'Bronze',
    minValue: 0,
    maxValue: 10000,
    color: 'text-amber-400',
    bgColor: 'bg-amber-50 border-amber-200',
    badge: '🥉',
    benefits: ['1x points per purchase', 'Monthly newsletter', 'Email promotions'],
    pointsMultiplier: 1,
    customerCount: 342,
    totalMembers: 342,
  },
  {
    name: 'Silver',
    minValue: 10000,
    maxValue: 25000,
    color: 'text-slate-300',
    bgColor: 'bg-slate-50 border-slate-200',
    badge: '🥈',
    benefits: ['1.25x points', 'Birthday offer', 'Priority support', 'Free shipping'],
    pointsMultiplier: 1.25,
    customerCount: 156,
    totalMembers: 156,
  },
  {
    name: 'Gold',
    minValue: 25000,
    maxValue: 50000,
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-50 border-yellow-200',
    badge: '🥇',
    benefits: ['1.5x points', 'Exclusive sales', '10% loyalty discount', 'VIP support'],
    pointsMultiplier: 1.5,
    customerCount: 78,
    totalMembers: 78,
  },
  {
    name: 'Platinum',
    minValue: 50000,
    maxValue: 100000,
    color: 'text-blue-400',
    bgColor: 'bg-blue-50 border-blue-200',
    badge: '💎',
    benefits: ['2x points', '15% loyalty discount', 'Personal account manager', 'Event invites'],
    pointsMultiplier: 2,
    customerCount: 32,
    totalMembers: 32,
  },
  {
    name: 'Diamond',
    minValue: 100000,
    maxValue: Infinity,
    color: 'text-purple-400',
    bgColor: 'bg-purple-50 border-purple-200',
    badge: '👑',
    benefits: ['3x points', '20% loyalty discount', 'Concierge service', 'Exclusive products'],
    pointsMultiplier: 3,
    customerCount: 12,
    totalMembers: 12,
  },
];

const REWARDS = [
  { id: 1, name: 'Frame Discount', pointsCost: 500, discount: '10% off frames' },
  { id: 2, name: 'Lens Upgrade', pointsCost: 750, discount: 'Free premium lens upgrade' },
  { id: 3, name: 'Accessories Pack', pointsCost: 250, discount: 'Free accessories' },
  { id: 4, name: 'Eye Test', pointsCost: 1000, discount: 'Free comprehensive eye test' },
  { id: 5, name: 'Extended Warranty', pointsCost: 600, discount: '1 year warranty extension' },
  { id: 6, name: 'Home Delivery', pointsCost: 200, discount: 'Free home delivery' },
];

export function LoyaltyProgram() {
  const [activeTab, setActiveTab] = useState<'overview' | 'tiers' | 'rewards' | 'promotions'>('overview');
  const totalCustomers = LOYALTY_TIERS.reduce((sum, t) => sum + t.customerCount, 0);

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM · Loyalty</div>
          <h1>Reward what comes back.</h1>
          <div className="hint">5-tier system (Silver / Gold / Platinum / Diamond / VIP). Points on ₹100 spend, redeemable at POS with cap.</div>
        </div>
        <button className="btn sm">
          <Settings className="w-4 h-4" /> Program settings
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Total Members</p>
          <p className="text-2xl font-bold text-gray-900">{totalCustomers}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Points Issued</p>
          <p className="text-2xl font-bold text-green-400">2.4M</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Points Redeemed</p>
          <p className="text-2xl font-bold text-orange-400">680K</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Redemption Rate</p>
          <p className="text-2xl font-bold text-blue-400">28%</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['overview', 'tiers', 'rewards', 'promotions'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-500 hover:text-gray-600'
            )}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Tier Distribution */}
          <div className="bg-white border border-gray-200 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Member Distribution</h3>
            <div className="space-y-4">
              {LOYALTY_TIERS.map((tier) => {
                const percentage = (tier.customerCount / totalCustomers) * 100;
                return (
                  <div key={tier.name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm">
                        {tier.badge} <span className={clsx('font-semibold', tier.color)}>{tier.name}</span>
                      </span>
                      <span className="text-sm text-gray-500">{tier.customerCount}</span>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                      <div
                        className="bg-gradient-to-r from-blue-500 to-purple-500 h-full"
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Points Overview */}
          <div className="bg-white border border-gray-200 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Points Overview</h3>
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                <span className="text-gray-500">Points Issued (30 days)</span>
                <span className="text-gray-900 font-semibold">458K</span>
              </div>
              <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                <span className="text-gray-500">Points Redeemed (30 days)</span>
                <span className="text-gray-900 font-semibold">142K</span>
              </div>
              <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                <span className="text-gray-500">Active Points Balance</span>
                <span className="text-green-400 font-semibold">1.72M</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Avg Points/Member</span>
                <span className="text-blue-400 font-semibold">14,200</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'tiers' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
          {LOYALTY_TIERS.map((tier) => (
            <div key={tier.name} className={clsx('rounded-lg p-4 border', tier.bgColor)}>
              <div className="text-3xl mb-2">{tier.badge}</div>
              <h3 className={clsx('text-lg font-bold mb-2', tier.color)}>{tier.name}</h3>
              <p className="text-xs text-gray-500 mb-3">
                ₹{tier.minValue.toLocaleString('en-IN')} - ₹{tier.maxValue === Infinity ? '∞' : tier.maxValue.toLocaleString('en-IN')}
              </p>

              <div className="space-y-2 mb-4 pb-4 border-b border-gray-200">
                {tier.benefits.map((benefit, idx) => (
                  <p key={idx} className="text-xs text-gray-600">✓ {benefit}</p>
                ))}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Members</span>
                  <span className="text-gray-900 font-semibold">{tier.customerCount}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Points 3x</span>
                  <span className="text-green-400 font-semibold">{tier.pointsMultiplier}x</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {activeTab === 'rewards' && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Available Rewards</h3>
            <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold flex items-center gap-2">
              <Plus className="w-4 h-4" />
              Add Reward
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {REWARDS.map((reward) => (
              <div key={reward.id} className="bg-white border border-gray-200 rounded-lg p-4 hover:border-gray-300 cursor-pointer">
                <div className="flex items-start justify-between mb-3">
                  <h4 className="text-gray-900 font-semibold">{reward.name}</h4>
                  <Gift className="w-5 h-5 text-yellow-400" />
                </div>
                <p className="text-gray-500 text-sm mb-4">{reward.discount}</p>
                <div className="flex items-center justify-between">
                  <span className="text-2xl font-bold text-green-400">{reward.pointsCost}</span>
                  <span className="text-xs text-gray-500">points</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'promotions' && (
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Active Promotions</h3>
            <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold">
              Create Campaign
            </button>
          </div>
          <div className="space-y-3">
            {[
              { name: 'Double Points - Valentine', status: 'Active', ends: 'Feb 14' },
              { name: 'Tier Up Challenge', status: 'Active', ends: 'Mar 1' },
              { name: 'Refer & Earn', status: 'Scheduled', ends: 'Mar 15' },
            ].map((promo, idx) => (
              <div key={idx} className="flex items-center justify-between p-3 bg-gray-100 rounded">
                <div>
                  <p className="text-gray-900 font-semibold">{promo.name}</p>
                  <p className="text-xs text-gray-500">Ends {promo.ends}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className={clsx(
                    'px-2 py-1 rounded text-xs font-semibold',
                    promo.status === 'Active' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'
                  )}>
                    {promo.status}
                  </span>
                  <Edit className="w-4 h-4 text-gray-500 cursor-pointer hover:text-gray-600" />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default LoyaltyProgram;

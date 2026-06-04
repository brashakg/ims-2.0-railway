// ============================================================================
// IMS 2.0 - Loyalty Program Management
// ============================================================================
// 4-tier loyalty system: Bronze/Silver/Gold/Platinum (driven by the engine)

import { useState, useEffect } from 'react';
import { Plus, Settings } from 'lucide-react';
import clsx from 'clsx';
import { loyaltyApi, type LoyaltyProgramStats, type LoyaltySettings } from '../../services/api/loyalty';

// Visual metadata only (badge / colour / benefits). The real numeric
// thresholds + point multipliers come from the loyalty engine
// (loyaltyApi.getSettings -> tier_thresholds / tier_multipliers), so the UI
// never disagrees with what actually earns/tiers a customer. 4 tiers — the
// engine has no "Diamond".
interface LoyaltyTierMeta {
  name: string;
  key: string; // engine tier key (UPPER) for thresholds/multipliers lookup
  color: string;
  bgColor: string;
  badge: string;
  benefits: string[];
}

const LOYALTY_TIERS: LoyaltyTierMeta[] = [
  {
    name: 'Bronze',
    key: 'BRONZE',
    color: 'text-amber-600',
    bgColor: 'bg-amber-50 border-amber-200',
    badge: '🥉',
    benefits: ['Base points per purchase', 'Monthly newsletter', 'Email promotions'],
  },
  {
    name: 'Silver',
    key: 'SILVER',
    color: 'text-slate-700',
    bgColor: 'bg-slate-50 border-slate-200',
    badge: '🥈',
    benefits: ['Birthday offer', 'Priority support', 'Free shipping'],
  },
  {
    name: 'Gold',
    key: 'GOLD',
    color: 'text-yellow-600',
    bgColor: 'bg-yellow-50 border-yellow-200',
    badge: '🥇',
    benefits: ['Exclusive sales', 'Loyalty discount', 'VIP support'],
  },
  {
    name: 'Platinum',
    key: 'PLATINUM',
    color: 'text-blue-600',
    bgColor: 'bg-blue-50 border-blue-200',
    badge: '💎',
    benefits: ['Top loyalty discount', 'Personal account manager', 'Event invites'],
  },
];

const fmtCompact = (n: number) =>
  new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 }).format(n || 0);
const tierCount = (stats: LoyaltyProgramStats | null, tierName: string) =>
  stats?.by_tier?.[tierName.toUpperCase()] ?? 0;

export function LoyaltyProgram() {
  const [activeTab, setActiveTab] = useState<'overview' | 'tiers' | 'rewards' | 'promotions'>('overview');
  const [stats, setStats] = useState<LoyaltyProgramStats | null>(null);
  const [settings, setSettings] = useState<LoyaltySettings | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([loyaltyApi.getProgramStats(), loyaltyApi.getSettings()])
      .then(([statsRes, settingsRes]) => {
        if (!alive) return;
        setStats(statsRes.status === 'fulfilled' ? statsRes.value : null);
        setSettings(settingsRes.status === 'fulfilled' ? settingsRes.value : null);
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  return (
    <div className="inv-body" aria-busy={loading ? "true" : "false"}>
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">CRM · Loyalty</div>
          <h1>Reward what comes back.</h1>
          <div className="hint">4-tier system (Bronze / Silver / Gold / Platinum). Points earned on spend, redeemable at POS with cap.</div>
        </div>
        <button className="btn sm">
          <Settings className="w-4 h-4" /> Program settings
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Total Members</p>
          <p className="text-2xl font-bold text-gray-900">{(stats?.total_members ?? 0).toLocaleString('en-IN')}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Points Issued</p>
          <p className="text-2xl font-bold text-green-600">{fmtCompact(stats?.points_issued ?? 0)}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Points Redeemed</p>
          <p className="text-2xl font-bold text-orange-600">{fmtCompact(stats?.points_redeemed ?? 0)}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Redemption Rate</p>
          <p className="text-2xl font-bold text-blue-600">{stats?.redemption_rate ?? 0}%</p>
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
                ? 'border-blue-500 text-blue-600'
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
                const count = tierCount(stats, tier.name);
                const total = stats?.total_members ?? 0;
                const percentage = total > 0 ? (count / total) * 100 : 0;
                return (
                  <div key={tier.name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm">
                        {tier.badge} <span className={clsx('font-semibold', tier.color)}>{tier.name}</span>
                      </span>
                      <span className="text-sm text-gray-500">{count}</span>
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
                <span className="text-gray-500">Points Issued (lifetime)</span>
                <span className="text-gray-900 font-semibold">{fmtCompact(stats?.points_issued ?? 0)}</span>
              </div>
              <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                <span className="text-gray-500">Points Redeemed (lifetime)</span>
                <span className="text-gray-900 font-semibold">{fmtCompact(stats?.points_redeemed ?? 0)}</span>
              </div>
              <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                <span className="text-gray-500">Active Points Balance</span>
                <span className="text-green-600 font-semibold">{fmtCompact(stats?.active_points_balance ?? 0)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Avg Points/Member</span>
                <span className="text-blue-600 font-semibold">{(stats?.avg_points_per_member ?? 0).toLocaleString('en-IN')}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'tiers' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {LOYALTY_TIERS.map((tier) => {
            // Threshold + multiplier come from the engine config, not hardcoded.
            const threshold = settings?.tier_thresholds?.[tier.key];
            const multiplier = settings?.tier_multipliers?.[tier.key];
            return (
              <div key={tier.name} className={clsx('rounded-lg p-4 border', tier.bgColor)}>
                <div className="text-3xl mb-2">{tier.badge}</div>
                <h3 className={clsx('text-lg font-bold mb-2', tier.color)}>{tier.name}</h3>
                <p className="text-xs text-gray-500 mb-3">
                  {threshold !== undefined
                    ? `${threshold.toLocaleString('en-IN')}+ lifetime points`
                    : 'Threshold from program settings'}
                </p>

                <div className="space-y-2 mb-4 pb-4 border-b border-gray-200">
                  {tier.benefits.map((benefit, idx) => (
                    <p key={idx} className="text-xs text-gray-600">✓ {benefit}</p>
                  ))}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500">Members</span>
                    <span className="text-gray-900 font-semibold">{tierCount(stats, tier.name)}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500">Points multiplier</span>
                    <span className="text-green-600 font-semibold">
                      {multiplier !== undefined ? `${multiplier}x` : '—'}
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
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
          <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-gray-500">
            No rewards configured yet. Reward catalog management is coming soon.
          </div>
        </div>
      )}

      {activeTab === 'promotions' && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Active Promotions</h3>
            <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold">
              Create Campaign
            </button>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-gray-500">
            No active promotions. Loyalty promotions are coming soon.
          </div>
        </div>
      )}
    </div>
  );
}

export default LoyaltyProgram;

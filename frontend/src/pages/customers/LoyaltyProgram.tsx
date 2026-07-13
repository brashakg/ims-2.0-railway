// ============================================================================
// IMS 2.0 - Loyalty Program Management
// ============================================================================
// 4-tier loyalty system: Bronze/Silver/Gold/Platinum (driven by the engine)

import { useState, useEffect } from 'react';
import { Plus, Settings, Trash2 } from 'lucide-react';
import clsx from 'clsx';
import { loyaltyApi, type LoyaltyProgramStats, type LoyaltySettings, type LoyaltyReward, type LoyaltyRewardCreate } from '../../services/api/loyalty';
import { useToast } from '../../context/ToastContext';

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
    color: 'text-gray-700',
    bgColor: 'bg-gray-50 border-gray-200',
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

// CRM-13: reward type badge colours (neutral palette, no cartoonish multi-colour)
const REWARD_TYPE_STYLE: Record<string, string> = {
  DISCOUNT: 'bg-gray-100 text-gray-700',
  FREE_ITEM: 'bg-gray-100 text-gray-700',
  VOUCHER: 'bg-gray-100 text-gray-700',
  EXPERIENCE: 'bg-gray-100 text-gray-700',
};

const REWARD_TYPE_LABEL: Record<string, string> = {
  DISCOUNT: 'Discount',
  FREE_ITEM: 'Free item',
  VOUCHER: 'Voucher',
  EXPERIENCE: 'Experience',
};

const BLANK_REWARD: LoyaltyRewardCreate = {
  name: '',
  type: 'DISCOUNT',
  point_cost: 100,
  description: '',
};

export function LoyaltyProgram() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'overview' | 'tiers' | 'rewards' | 'promotions'>('overview');
  const [stats, setStats] = useState<LoyaltyProgramStats | null>(null);
  const [settings, setSettings] = useState<LoyaltySettings | null>(null);
  const [loading, setLoading] = useState(true);

  // CRM-13: reward catalog state
  const [rewards, setRewards] = useState<LoyaltyReward[]>([]);
  const [rewardsLoading, setRewardsLoading] = useState(false);
  const [showAddReward, setShowAddReward] = useState(false);
  const [newReward, setNewReward] = useState<LoyaltyRewardCreate>(BLANK_REWARD);
  const [savingReward, setSavingReward] = useState(false);

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

  // Load rewards when the tab is opened
  useEffect(() => {
    if (activeTab !== 'rewards') return;
    let alive = true;
    setRewardsLoading(true);
    loyaltyApi.listRewards({ active_only: false })
      .then(res => { if (alive) setRewards(res.rewards || []); })
      .catch(() => { if (alive) toast.error('Failed to load reward catalog'); })
      .finally(() => { if (alive) setRewardsLoading(false); });
    return () => { alive = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const handleAddReward = async () => {
    if (!newReward.name.trim()) { toast.error('Reward name is required'); return; }
    if (newReward.point_cost < 1) { toast.error('Point cost must be at least 1'); return; }
    setSavingReward(true);
    try {
      const res = await loyaltyApi.createReward(newReward);
      setRewards(prev => [res.reward, ...prev]);
      setShowAddReward(false);
      setNewReward(BLANK_REWARD);
      toast.success('Reward added');
    } catch {
      toast.error('Failed to create reward');
    } finally {
      setSavingReward(false);
    }
  };

  const handleToggleReward = async (reward: LoyaltyReward) => {
    try {
      const res = await loyaltyApi.updateReward(reward.reward_id, { active: !reward.active });
      setRewards(prev => prev.map(r => r.reward_id === reward.reward_id ? res.reward : r));
      toast.success(res.reward.active ? 'Reward activated' : 'Reward deactivated');
    } catch {
      toast.error('Failed to update reward');
    }
  };

  const handleDeleteReward = async (rewardId: string) => {
    try {
      await loyaltyApi.deleteReward(rewardId);
      setRewards(prev => prev.filter(r => r.reward_id !== rewardId));
      toast.success('Reward deleted');
    } catch {
      toast.error('Failed to delete reward');
    }
  };

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
          <p className="text-2xl font-bold text-amber-600">{fmtCompact(stats?.points_redeemed ?? 0)}</p>
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
                        className="bg-blue-500 h-full"
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

      {/* CRM-13: Reward catalog — wired to GET/POST /loyalty/rewards */}
      {activeTab === 'rewards' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-gray-900">Reward Catalog</h3>
            <button
              type="button"
              className="btn sm"
              onClick={() => setShowAddReward(v => !v)}
            >
              <Plus className="w-4 h-4" />
              {showAddReward ? 'Cancel' : 'Add Reward'}
            </button>
          </div>

          {/* Add reward form */}
          {showAddReward && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
              <p className="text-sm font-semibold text-gray-700">New reward</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Name</label>
                  <input
                    className="input-field text-sm"
                    value={newReward.name}
                    onChange={e => setNewReward(p => ({ ...p, name: e.target.value }))}
                    placeholder="e.g. Free glasses-cloth"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Type</label>
                  <select
                    className="input-field text-sm"
                    title="Reward type"
                    value={newReward.type}
                    onChange={e => setNewReward(p => ({ ...p, type: e.target.value as LoyaltyRewardCreate['type'] }))}
                  >
                    <option value="DISCOUNT">Discount</option>
                    <option value="FREE_ITEM">Free Item</option>
                    <option value="VOUCHER">Voucher</option>
                    <option value="EXPERIENCE">Experience</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Point Cost</label>
                  <input
                    type="number"
                    className="input-field text-sm"
                    value={newReward.point_cost}
                    min={1}
                    onChange={e => setNewReward(p => ({ ...p, point_cost: Number(e.target.value) }))}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Cash Value (Rs., optional)</label>
                  <input
                    type="number"
                    className="input-field text-sm"
                    value={newReward.cash_value ?? ''}
                    min={0}
                    title="Cash equivalent value in rupees"
                    onChange={e => setNewReward(p => ({ ...p, cash_value: e.target.value ? Number(e.target.value) : undefined }))}
                    placeholder="Optional"
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="block text-xs text-gray-500 mb-1">Description (optional)</label>
                  <input
                    className="input-field text-sm"
                    value={newReward.description ?? ''}
                    onChange={e => setNewReward(p => ({ ...p, description: e.target.value }))}
                    placeholder="What does the customer get?"
                  />
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button type="button" className="btn sm outline" onClick={() => setShowAddReward(false)}>Cancel</button>
                <button type="button" className="btn sm" onClick={handleAddReward} disabled={savingReward}>
                  {savingReward ? 'Saving…' : 'Save Reward'}
                </button>
              </div>
            </div>
          )}

          {rewardsLoading ? (
            <div className="text-center text-gray-500 py-10 text-sm">Loading…</div>
          ) : rewards.length === 0 ? (
            <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-gray-500 text-sm">
              No rewards configured yet. Use the button above to add the first reward.
            </div>
          ) : (
            <div className="space-y-2">
              {rewards.map(reward => (
                <div key={reward.reward_id} className="bg-white border border-gray-200 rounded-lg p-4 flex items-center gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-gray-900 text-sm">{reward.name}</span>
                      <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', REWARD_TYPE_STYLE[reward.type] ?? 'bg-gray-100 text-gray-700')}>
                        {REWARD_TYPE_LABEL[reward.type] ?? reward.type}
                      </span>
                      {!reward.active && (
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-400">Inactive</span>
                      )}
                    </div>
                    {reward.description && <p className="text-xs text-gray-500 mt-0.5">{reward.description}</p>}
                    <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                      <span>{reward.point_cost.toLocaleString('en-IN')} pts</span>
                      {reward.cash_value !== undefined && reward.cash_value !== null && (
                        <span>Rs.{reward.cash_value}</span>
                      )}
                      {reward.max_redemptions && (
                        <span>{reward.redemption_count}/{reward.max_redemptions} redeemed</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      className="text-xs text-gray-500 hover:text-gray-800 border border-gray-200 rounded px-2 py-1"
                      onClick={() => handleToggleReward(reward)}
                    >
                      {reward.active ? 'Deactivate' : 'Activate'}
                    </button>
                    <button
                      type="button"
                      className="text-gray-400 hover:text-red-600"
                      onClick={() => handleDeleteReward(reward.reward_id)}
                      aria-label="Delete reward"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'promotions' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-gray-900">Active Promotions</h3>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-gray-500 text-sm">
            <p className="font-medium text-gray-700 mb-1">Promotions run via the Campaign Manager</p>
            <p className="text-xs text-gray-500">
              Go to Marketing &rarr; Campaign Manager to create BOGO, combo, or threshold campaigns
              linked to a loyalty audience segment.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default LoyaltyProgram;

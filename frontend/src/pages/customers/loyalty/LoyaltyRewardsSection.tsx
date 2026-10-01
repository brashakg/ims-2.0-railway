// /customers/loyalty/rewards -- the reward catalog (CRM-13, wired to
// GET/POST /loyalty/rewards). State, handlers and JSX moved byte-identical
// from the old LoyaltyProgram 'rewards' tab; the one change is the load
// effect, which ran when the tab was opened and now runs on arrival.

import { useState, useEffect } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import clsx from 'clsx';
import { loyaltyApi, type LoyaltyReward, type LoyaltyRewardCreate } from '../../../services/api/loyalty';
import { useToast } from '../../../context/ToastContext';
import { BLANK_REWARD, useLoyaltyContext } from './loyaltyShared';

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

export function LoyaltyRewardsSection() {
  const toast = useToast();
  // The draft form lives in the layout (survives Overview / Tiers visits).
  const { rewardDraft: { showAddReward, setShowAddReward, newReward, setNewReward } } = useLoyaltyContext();

  // CRM-13: reward catalog state
  const [rewards, setRewards] = useState<LoyaltyReward[]>([]);
  const [rewardsLoading, setRewardsLoading] = useState(false);
  const [savingReward, setSavingReward] = useState(false);

  // Load rewards on arrival
  useEffect(() => {
    let alive = true;
    setRewardsLoading(true);
    loyaltyApi.listRewards({ active_only: false })
      .then(res => { if (alive) setRewards(res.rewards || []); })
      .catch(() => { if (alive) toast.error('Failed to load reward catalog'); })
      .finally(() => { if (alive) setRewardsLoading(false); });
    return () => { alive = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  );
}

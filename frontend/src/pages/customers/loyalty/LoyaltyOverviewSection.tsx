// /customers/loyalty -- Overview (the index): member distribution by tier and
// the lifetime points picture. Moved byte-identical from the old
// LoyaltyProgram 'overview' tab.

import clsx from 'clsx';
import { LOYALTY_TIERS, fmtCompact, tierCount, useLoyaltyContext } from './loyaltyShared';

export function LoyaltyOverviewSection() {
  const { stats } = useLoyaltyContext();

  return (
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
  );
}

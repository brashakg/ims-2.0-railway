// /customers/loyalty/tiers -- the four tier cards (threshold + multiplier from
// the engine settings, member count from the stats). Moved byte-identical
// from the old LoyaltyProgram 'tiers' tab.

import clsx from 'clsx';
import { LOYALTY_TIERS, tierCount, useLoyaltyContext } from './loyaltyShared';

export function LoyaltyTiersSection() {
  const { stats, settings } = useLoyaltyContext();

  return (
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
  );
}

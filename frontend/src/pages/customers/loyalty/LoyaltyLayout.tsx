// ============================================================================
// IMS 2.0 - Loyalty Program Management (layout)
// ============================================================================
// 4-tier loyalty system: Bronze/Silver/Gold/Platinum (driven by the engine)
//
// Wave 6 B12 split (the recipe ExpensesLayout set): the tabs that lived in
// useState behind one URL are real pages now, one URL each:
//   /customers/loyalty (Overview) · /tiers · /rewards
// The fourth tab, 'promotions', was a placeholder pointing at the Campaign
// Manager (no data, no API call) and is gone. This layout keeps the header,
// the summary cards, the section nav and the one stats + settings load; it
// hands that data to the section pages through <Outlet context>. The role
// gate is the /customers/loyalty route's (routes/customerRoutes.tsx), which
// wraps the layout and so every section.

import { useState, useEffect } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Settings } from 'lucide-react';
import clsx from 'clsx';
import { loyaltyApi, type LoyaltyProgramStats, type LoyaltySettings } from '../../../services/api/loyalty';
import { fmtCompact, type LoyaltyOutletContext } from './loyaltyShared';

export function LoyaltyLayout() {
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

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk download spinner
  // (same owner feedback ExpensesLayout answers). Vite dedupes these against
  // the route-level lazy() imports -- no double download.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./LoyaltyOverviewSection');
      void import('./LoyaltyTiersSection');
      void import('./LoyaltyRewardsSection');
    });
  }, []);

  const sectionContext: LoyaltyOutletContext = { stats, settings };

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

      {/* Section nav -- real links, one URL per section. */}
      <div className="flex gap-2 border-b border-gray-200">
        {([
          ['', 'Overview'],
          ['tiers', 'Tiers'],
          ['rewards', 'Rewards'],
        ] as const).map(([seg, label]) => (
          <NavLink
            key={seg}
            to={seg ? `/customers/loyalty/${seg}` : '/customers/loyalty'}
            end
            className={({ isActive }) => clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              isActive
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-600'
            )}
          >
            {label}
          </NavLink>
        ))}
      </div>

      <Outlet context={sectionContext} />
    </div>
  );
}

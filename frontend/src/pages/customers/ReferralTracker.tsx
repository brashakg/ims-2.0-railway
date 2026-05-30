// ============================================================================
// IMS 2.0 - Referral Tracker
// ============================================================================
// Referral codes, chain tracking, rewards. Wired to GET /marketing/referrals
// (store-scoped). Aggregates (totals, leaderboard) are derived from the real
// referral records returned by the backend — no fabricated numbers.

import { useState, useEffect } from 'react';
import { Share2, Gift, Copy, Check, Trophy } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { marketingApi } from '../../services/api/marketing';

// Shape of a referral record as returned by GET /marketing/referrals.
interface Referral {
  referral_id: string;
  store_id?: string;
  referrer_customer_id?: string;
  referrer_name?: string;
  referrer_phone?: string;
  referral_code?: string;
  referee_customer_id?: string | null;
  referee_name?: string | null;
  status?: string; // INVITED | REWARD_CREDITED | ...
  reward_amount?: number;
  invite_sent_at?: string | null;
  reward_credited_at?: string | null;
  created_at?: string;
}

// A referrer aggregated from the real referral records (leaderboard).
interface ReferrerSummary {
  name: string;
  code: string;
  referrals: number;
  earnings: number;
}

const REWARDED_STATUSES = new Set(['REWARD_CREDITED', 'CONFIRMED', 'COMPLETED']);

function isRewarded(status?: string): boolean {
  return !!status && REWARDED_STATUSES.has(status.toUpperCase());
}

export function ReferralTracker() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'overview' | 'leaderboard' | 'history'>('overview');
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadReferrals();
    // Refetch when the active store changes so referrals follow the switcher.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId]);

  const loadReferrals = async () => {
    setLoading(true);
    try {
      const res = await marketingApi.getReferrals(user?.activeStoreId);
      setReferrals(Array.isArray(res?.referrals) ? res.referrals : []);
    } catch {
      setReferrals([]);
      toast.error('Failed to load referral data');
    } finally {
      setLoading(false);
    }
  };

  const copyCode = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  // --- Derived aggregates (all from the real referral records) ---
  const totalReferrals = referrals.length;
  const confirmedReferrals = referrals.filter((r) => isRewarded(r.status));
  const totalEarnings = confirmedReferrals.reduce((sum, r) => sum + (r.reward_amount || 0), 0);

  // Build a leaderboard by grouping records on the referrer.
  const referrerMap = new Map<string, ReferrerSummary>();
  for (const r of referrals) {
    const key = r.referrer_customer_id || r.referrer_name || r.referral_code || r.referral_id;
    if (!key) continue;
    const existing = referrerMap.get(key) || {
      name: r.referrer_name || 'Unknown',
      code: r.referral_code || '—',
      referrals: 0,
      earnings: 0,
    };
    existing.referrals += 1;
    if (isRewarded(r.status)) existing.earnings += r.reward_amount || 0;
    referrerMap.set(key, existing);
  }
  const referrers = Array.from(referrerMap.values()).sort((a, b) => b.referrals - a.referrals);

  const confirmedCount = confirmedReferrals.length;
  const conversionRate = totalReferrals > 0 ? Math.round((confirmedCount / totalReferrals) * 100) : 0;

  const formatDate = (value?: string | null) =>
    value ? new Date(value).toLocaleDateString() : '—';

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM · Referrals</div>
          <h1>Word of mouth, tracked.</h1>
          <div className="hint">Who sent whom. Leaderboard with referral count, conversion rate, and earned rewards paid via wallet credit.</div>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="bg-white rounded-lg p-4 border border-gray-200">
          <p className="text-gray-500 text-sm mb-1">Total Referrals</p>
          <p className="text-2xl font-bold text-gray-900">{totalReferrals}</p>
        </div>
        <div className="bg-white rounded-lg p-4 border border-gray-200">
          <p className="text-gray-500 text-sm mb-1">Active Referrers</p>
          <p className="text-2xl font-bold text-blue-600">{referrers.length}</p>
        </div>
        <div className="bg-white rounded-lg p-4 border border-gray-200">
          <p className="text-gray-500 text-sm mb-1">Total Rewards Given</p>
          <p className="text-2xl font-bold text-green-600">₹{totalEarnings.toLocaleString('en-IN')}</p>
        </div>
        <div className="bg-white rounded-lg p-4 border border-gray-200">
          <p className="text-gray-500 text-sm mb-1">Conversion Rate</p>
          <p className="text-2xl font-bold text-purple-600">{conversionRate}%</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['overview', 'leaderboard', 'history'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {tab === 'overview' ? 'Overview' : tab === 'leaderboard' ? 'Leaderboard' : 'History'}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-center text-gray-500 py-12 text-sm">Loading referrals…</div>
      ) : (
        <>
          {/* Tab Content */}
          {activeTab === 'overview' && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Share Your Code */}
              <div className="bg-white rounded-lg p-6 border border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                  <Share2 className="w-5 h-5" />
                  Referral Codes
                </h3>
                <p className="text-gray-500 text-sm mb-4">
                  Share a customer's unique code with friends and family so they earn rewards on purchases.
                </p>
                {referrers.length === 0 ? (
                  <div className="text-center text-gray-500 py-8 text-sm">
                    No referral codes issued yet. Send a referral invite from a customer's profile to generate one.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {referrers.slice(0, 5).map((ref) => (
                      <div key={ref.code} className="bg-gray-100 rounded p-3">
                        <p className="text-gray-500 text-xs mb-1">{ref.name}</p>
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            value={ref.code}
                            readOnly
                            className="flex-1 bg-white border border-gray-300 rounded px-3 py-2 text-gray-900 font-mono text-sm"
                          />
                          <button
                            onClick={() => copyCode(ref.code)}
                            className="p-2 bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                            aria-label={`Copy ${ref.code}`}
                          >
                            {copiedCode === ref.code ? <Check className="w-5 h-5" /> : <Copy className="w-5 h-5" />}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Reward Info */}
              <div className="bg-white rounded-lg p-6 border border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                  <Gift className="w-5 h-5" />
                  How Rewards Work
                </h3>
                <p className="text-gray-500 text-sm mb-4">
                  When a referred customer completes a qualifying purchase, the reward is credited to the
                  referrer's wallet (store credit + loyalty points) on redemption.
                </p>
                <div className="flex items-center justify-between p-3 bg-gray-100 rounded">
                  <div>
                    <p className="text-gray-900 font-semibold">Confirmed referrals</p>
                    <p className="text-gray-500 text-xs">Rewards already credited</p>
                  </div>
                  <p className="text-green-600 font-bold">{confirmedCount}</p>
                </div>
                <div className="flex items-center justify-between p-3 bg-gray-100 rounded mt-2">
                  <div>
                    <p className="text-gray-900 font-semibold">Pending</p>
                    <p className="text-gray-500 text-xs">Awaiting qualifying purchase</p>
                  </div>
                  <p className="text-gray-900 font-bold">{totalReferrals - confirmedCount}</p>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'leaderboard' && (
            <div className="bg-white rounded-lg p-6 border border-gray-200">
              <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Trophy className="w-5 h-5" />
                Top Referrers
              </h3>
              {referrers.length === 0 ? (
                <div className="text-center text-gray-500 py-8 text-sm">No referrers yet.</div>
              ) : (
                <div className="space-y-3">
                  {referrers.map((referrer, idx) => (
                    <div key={referrer.code + idx} className="flex items-center gap-4 p-4 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors">
                      <div className={clsx(
                        'w-10 h-10 rounded-full flex items-center justify-center font-bold text-gray-900',
                        idx === 0 ? 'bg-yellow-400' : idx === 1 ? 'bg-gray-300' : idx === 2 ? 'bg-orange-400' : 'bg-white border border-gray-200'
                      )}>
                        {idx + 1}
                      </div>
                      <div className="flex-1">
                        <p className="text-gray-900 font-semibold">{referrer.name}</p>
                        <p className="text-gray-500 text-xs">Code: {referrer.code}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-gray-900 font-bold">{referrer.referrals}</p>
                        <p className="text-gray-500 text-xs">referrals</p>
                      </div>
                      <div className="text-right min-w-fit">
                        <p className="text-green-600 font-bold">₹{referrer.earnings.toLocaleString('en-IN')}</p>
                        <p className="text-gray-500 text-xs">earned</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === 'history' && (
            <div className="bg-white rounded-lg p-6 border border-gray-200">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">Referral History</h3>
              {referrals.length === 0 ? (
                <div className="text-center text-gray-500 py-8 text-sm">No referrals yet.</div>
              ) : (
                <div className="space-y-3">
                  {referrals.map((referral) => {
                    const confirmed = isRewarded(referral.status);
                    return (
                      <div key={referral.referral_id} className="flex items-center justify-between p-4 bg-gray-100 rounded-lg">
                        <div>
                          <p className="text-gray-900 font-semibold">
                            {referral.referrer_name || 'Unknown'}
                            {referral.referee_name ? ` → ${referral.referee_name}` : ''}
                          </p>
                          <p className="text-gray-500 text-xs">
                            {referral.referral_code ? `${referral.referral_code} · ` : ''}
                            {formatDate(referral.created_at)}
                          </p>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className={clsx(
                            'px-2 py-1 rounded text-xs font-semibold',
                            confirmed ? 'bg-green-50 text-green-700' : 'bg-yellow-50 text-yellow-700'
                          )}>
                            {confirmed ? 'Confirmed' : 'Pending'}
                          </span>
                          <p className="text-green-600 font-bold min-w-fit">
                            +₹{(confirmed ? referral.reward_amount || 0 : 0).toLocaleString('en-IN')}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default ReferralTracker;

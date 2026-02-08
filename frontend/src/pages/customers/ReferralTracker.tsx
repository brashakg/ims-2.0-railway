// ============================================================================
// IMS 2.0 - Referral Tracker
// ============================================================================
// Referral codes, chain tracking, rewards, leaderboard, analytics

import { useState } from 'react';
import { Share2, Gift, Copy, Check, Trophy, Award } from 'lucide-react';
import clsx from 'clsx';

interface Referrer {
  id: string;
  name: string;
  code: string;
  referrals: number;
  earnings: number;
  topReward?: string;
}

interface Referral {
  id: string;
  referrer: string;
  referred: string;
  date: string;
  status: 'pending' | 'confirmed';
  reward: number;
}

const REFERRERS: Referrer[] = [
  {
    id: '1',
    name: 'Rajesh Kumar',
    code: 'RAJ2024',
    referrals: 24,
    earnings: 12000,
    topReward: 'Gold Tier',
  },
  {
    id: '2',
    name: 'Priya Sharma',
    code: 'PRIYA123',
    referrals: 18,
    earnings: 9000,
    topReward: 'Silver Tier',
  },
  {
    id: '3',
    name: 'Amit Patel',
    code: 'AMIT456',
    referrals: 15,
    earnings: 7500,
    topReward: 'Silver Tier',
  },
  {
    id: '4',
    name: 'Sunita Singh',
    code: 'SUNI789',
    referrals: 12,
    earnings: 6000,
    topReward: 'Silver Tier',
  },
  {
    id: '5',
    name: 'Vikram Desai',
    code: 'VIK2025',
    referrals: 8,
    earnings: 4000,
    topReward: 'Bronze Tier',
  },
];

const REFERRAL_HISTORY: Referral[] = [
  {
    id: '1',
    referrer: 'Rajesh Kumar',
    referred: 'Neha Singh',
    date: '2024-02-01',
    status: 'confirmed',
    reward: 500,
  },
  {
    id: '2',
    referrer: 'Priya Sharma',
    referred: 'Akshay Gupta',
    date: '2024-01-28',
    status: 'confirmed',
    reward: 500,
  },
  {
    id: '3',
    referrer: 'Rajesh Kumar',
    referred: 'Anjali Verma',
    date: '2024-01-25',
    status: 'pending',
    reward: 0,
  },
];

export function ReferralTracker() {
  const [activeTab, setActiveTab] = useState<'overview' | 'leaderboard' | 'history' | 'rewards'>('overview');
  const [copiedCode, setCopiedCode] = useState<string | null>(null);

  const copyCode = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  const totalReferrals = REFERRERS.reduce((sum, r) => sum + r.referrals, 0);
  const totalEarnings = REFERRERS.reduce((sum, r) => sum + r.earnings, 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">Referral Tracker</h1>
        <p className="text-gray-400">Customer referral program analytics and management</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Referrals</p>
          <p className="text-2xl font-bold text-white">{totalReferrals}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Active Referrers</p>
          <p className="text-2xl font-bold text-blue-400">{REFERRERS.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Rewards Given</p>
          <p className="text-2xl font-bold text-green-400">₹{totalEarnings.toLocaleString('en-IN')}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Conversion Rate</p>
          <p className="text-2xl font-bold text-purple-400">34%</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['overview', 'leaderboard', 'history', 'rewards'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab === 'overview' ? 'Overview' : tab === 'leaderboard' ? 'Leaderboard' : tab === 'history' ? 'History' : 'Rewards'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Share Your Code */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Share2 className="w-5 h-5" />
              Share Your Referral Code
            </h3>
            <p className="text-gray-400 text-sm mb-4">
              Share your unique code with friends and family to earn rewards on their purchases.
            </p>
            <div className="bg-gray-700 rounded p-4 mb-4">
              <p className="text-gray-400 text-xs mb-2">Your Referral Code</p>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value="RAJ2024"
                  readOnly
                  className="flex-1 bg-gray-600 border border-gray-500 rounded px-3 py-2 text-white font-mono"
                />
                <button
                  onClick={() => copyCode('RAJ2024')}
                  className="p-2 bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                >
                  {copiedCode === 'RAJ2024' ? <Check className="w-5 h-5" /> : <Copy className="w-5 h-5" />}
                </button>
              </div>
            </div>
            <button className="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center justify-center gap-2">
              <Share2 className="w-4 h-4" />
              Share Now
            </button>
          </div>

          {/* Top Rewards */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Gift className="w-5 h-5" />
              Reward Tiers
            </h3>
            <div className="space-y-3">
              {[
                { level: 'Bronze', referrals: '5+', reward: '₹500' },
                { level: 'Silver', referrals: '10+', reward: '₹1,200' },
                { level: 'Gold', referrals: '20+', reward: '₹3,000' },
                { level: 'Platinum', referrals: '30+', reward: '₹6,000+' },
              ].map((tier) => (
                <div key={tier.level} className="flex items-center justify-between p-3 bg-gray-700 rounded">
                  <div>
                    <p className="text-white font-semibold">{tier.level}</p>
                    <p className="text-gray-400 text-xs">{tier.referrals} referrals</p>
                  </div>
                  <p className="text-green-400 font-bold">{tier.reward}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'leaderboard' && (
        <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Trophy className="w-5 h-5" />
            Top Referrers
          </h3>
          <div className="space-y-3">
            {REFERRERS.map((referrer, idx) => (
              <div key={referrer.id} className="flex items-center gap-4 p-4 bg-gray-700 rounded-lg hover:bg-gray-600 transition-colors">
                <div className={clsx(
                  'w-10 h-10 rounded-full flex items-center justify-center font-bold text-white',
                  idx === 0 ? 'bg-yellow-600' : idx === 1 ? 'bg-gray-500' : idx === 2 ? 'bg-orange-600' : 'bg-gray-700 border border-gray-600'
                )}>
                  {idx === 0 ? '🥇' : idx === 1 ? '🥈' : idx === 2 ? '🥉' : idx + 1}
                </div>
                <div className="flex-1">
                  <p className="text-white font-semibold">{referrer.name}</p>
                  <p className="text-gray-400 text-xs">Code: {referrer.code}</p>
                </div>
                <div className="text-right">
                  <p className="text-white font-bold">{referrer.referrals}</p>
                  <p className="text-gray-400 text-xs">referrals</p>
                </div>
                <div className="text-right min-w-fit">
                  <p className="text-green-400 font-bold">₹{referrer.earnings}</p>
                  <p className="text-gray-400 text-xs">earned</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'history' && (
        <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
          <h3 className="text-lg font-semibold text-white mb-4">Referral History</h3>
          <div className="space-y-3">
            {REFERRAL_HISTORY.map((referral) => (
              <div key={referral.id} className="flex items-center justify-between p-4 bg-gray-700 rounded-lg">
                <div>
                  <p className="text-white font-semibold">{referral.referrer} → {referral.referred}</p>
                  <p className="text-gray-400 text-xs">{new Date(referral.date).toLocaleDateString()}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className={clsx(
                    'px-2 py-1 rounded text-xs font-semibold',
                    referral.status === 'confirmed' ? 'bg-green-900 text-green-300' : 'bg-yellow-900 text-yellow-300'
                  )}>
                    {referral.status === 'confirmed' ? 'Confirmed' : 'Pending'}
                  </span>
                  <p className="text-green-400 font-bold min-w-fit">+₹{referral.reward}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'rewards' && (
        <div className="space-y-4">
          <div className="bg-blue-900/30 border border-blue-700 rounded-lg p-4">
            <p className="text-blue-300 text-sm flex items-center gap-2">
              <Award className="w-4 h-4" />
              Reward structure: ₹500 per confirmed referral + tier bonuses
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {[
              { name: 'Direct Referral', value: '₹500' },
              { name: 'Bronze Tier Bonus', value: '₹500' },
              { name: 'Silver Tier Bonus', value: '₹2,000' },
              { name: 'Gold Tier Bonus', value: '₹5,000' },
            ].map((reward) => (
              <div key={reward.name} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p className="text-gray-400 text-sm mb-1">{reward.name}</p>
                <p className="text-2xl font-bold text-green-400">{reward.value}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default ReferralTracker;

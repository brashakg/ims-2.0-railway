// ============================================================================
// IMS 2.0 - Staff Incentive Tracking Dashboard
// ============================================================================
// Real-time incentive calculations with kicker tracking and leaderboard

import { useState, useEffect } from 'react';
import {
  TrendingUp,
  Target,
  Zap,
  Medal,
  Award,
  AlertCircle,
  Gift,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

// API endpoint structure (will implement in api.ts)
const incentivesApi = {
  getDashboard: async (month?: number, year?: number) => {
    const params = new URLSearchParams();
    if (month) params.append('month', String(month));
    if (year) params.append('year', String(year));
    const response = await fetch(`/api/v1/incentives/dashboard?${params}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
    });
    return response.json();
  },
  
  getLeaderboard: async (month?: number, year?: number) => {
    const params = new URLSearchParams();
    if (month) params.append('month', String(month));
    if (year) params.append('year', String(year));
    const response = await fetch(`/api/v1/incentives/leaderboard?${params}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
    });
    return response.json();
  },

  getKickers: async (staffId: string, month?: number, year?: number) => {
    const params = new URLSearchParams();
    if (month) params.append('month', String(month));
    if (year) params.append('year', String(year));
    const response = await fetch(`/api/v1/incentives/kickers/${staffId}?${params}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
    });
    return response.json();
  },
};

interface IncentiveDashboardData {
  staff_id: string;
  staff_name: string;
  month: number;
  year: number;
  target_amount: number;
  actual_sales: number;
  achievement_percentage: number;
  base_incentive: number;
  kicker_count: number;
  kicker_bonus: number;
  google_reviews: number;
  google_review_bonus: number;
  total_incentive: number;
  status: string;
  next_slab?: {
    current_slab: string;
    next_milestone: string;
  };
}

interface KickerData {
  staff_id: string;
  staff_name: string;
  month: number;
  year: number;
  total_kickers: number;
  total_sales: number;
  kicker_bonus: number;
  brand_summary: Record<string, number>;
  kickers: Array<{
    kicker_id: string;
    brand: string;
    product_name: string;
    sale_amount: number;
    sale_date: string;
  }>;
}

interface LeaderboardEntry {
  rank: number;
  staff_id: string;
  staff_name: string;
  achievement_percentage: number;
  actual_sales: number;
  target_amount: number;
  total_incentive: number;
  status: string;
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const getCurrentMonthYear = () => {
  const today = new Date();
  return { month: today.getMonth() + 1, year: today.getFullYear() };
};

export function IncentiveDashboard() {
  const { user } = useAuth();
  const { month: currentMonth, year: currentYear } = getCurrentMonthYear();

  // State
  const [selectedMonth, setSelectedMonth] = useState(currentMonth);
  const [selectedYear, setSelectedYear] = useState(currentYear);
  const [dashboardData, setDashboardData] = useState<IncentiveDashboardData | null>(null);
  const [kickerData, setKickerData] = useState<KickerData | null>(null);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load data
  useEffect(() => {
    loadData();
  }, [selectedMonth, selectedYear]);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const [dashboard, kickers, leaderboardData] = await Promise.all([
        incentivesApi.getDashboard(selectedMonth, selectedYear).catch(() => null),
        user?.id ? incentivesApi.getKickers(user.id, selectedMonth, selectedYear).catch(() => null) : null,
        incentivesApi.getLeaderboard(selectedMonth, selectedYear).catch(() => null),
      ]);

      setDashboardData(dashboard);
      setKickerData(kickers);
      setLeaderboard(leaderboardData?.leaderboard || []);
    } catch (err) {
      setError('Failed to load incentive data');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const getSlab = (percentage: number) => {
    if (percentage >= 120) return { label: '1.5% rate', color: 'text-yellow-400' };
    if (percentage >= 100) return { label: '1% rate', color: 'text-green-400' };
    if (percentage >= 80) return { label: '0.8% rate', color: 'text-blue-400' };
    return { label: 'Below 80%', color: 'text-red-400' };
  };

  const monthName = MONTHS[selectedMonth - 1];
  const data = dashboardData;

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <TrendingUp className="w-6 h-6 text-yellow-500" />
            Incentive Tracking
          </h1>
          <p className="text-gray-400">Sales targets, kickers, and earnings dashboard</p>
        </div>

        {/* Month/Year Selector */}
        <div className="flex items-center gap-3">
          <select
            value={selectedMonth}
            onChange={(e) => setSelectedMonth(Number(e.target.value))}
            className="px-3 py-2 bg-gray-700 text-white rounded-lg border border-gray-600 focus:border-yellow-500 outline-none"
          >
            {MONTHS.map((m, i) => (
              <option key={i} value={i + 1}>{m}</option>
            ))}
          </select>
          <select
            value={selectedYear}
            onChange={(e) => setSelectedYear(Number(e.target.value))}
            className="px-3 py-2 bg-gray-700 text-white rounded-lg border border-gray-600 focus:border-yellow-500 outline-none"
          >
            {[2024, 2025, 2026].map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-700 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 mt-0.5 flex-shrink-0" />
          <p className="text-red-400">{error}</p>
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="bg-gray-800 rounded-lg p-6 animate-pulse">
              <div className="h-4 bg-gray-700 rounded w-1/2 mb-4" />
              <div className="h-8 bg-gray-700 rounded w-3/4" />
            </div>
          ))}
        </div>
      ) : data ? (
        <>
          {/* Stats Cards */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-4 gap-4">
            {/* Sales Achievement */}
            <div className="bg-gradient-to-br from-gray-800 to-gray-900 rounded-lg p-6 border border-gray-700">
              <div className="flex items-center justify-between mb-4">
                <span className="text-gray-400 text-sm font-medium">Achievement %</span>
                <Target className="w-5 h-5 text-blue-400" />
              </div>
              <div className="text-3xl font-bold text-white mb-2">
                {data.achievement_percentage.toFixed(1)}%
              </div>
              <div className="text-xs text-gray-400">
                ₹{data.actual_sales.toLocaleString()} / ₹{data.target_amount.toLocaleString()}
              </div>
              <div className="mt-3 w-full bg-gray-700 rounded-full h-2">
                <div
                  className={clsx(
                    'h-2 rounded-full transition-all',
                    data.achievement_percentage >= 120 ? 'bg-yellow-500' :
                    data.achievement_percentage >= 100 ? 'bg-green-500' :
                    data.achievement_percentage >= 80 ? 'bg-blue-500' :
                    'bg-red-500'
                  )}
                  style={{ width: `${Math.min(data.achievement_percentage, 150)}%` }}
                />
              </div>
            </div>

            {/* Base Incentive */}
            <div className="bg-gradient-to-br from-gray-800 to-gray-900 rounded-lg p-6 border border-gray-700">
              <div className="flex items-center justify-between mb-4">
                <span className="text-gray-400 text-sm font-medium">Base Incentive</span>
                <Gift className="w-5 h-5 text-green-400" />
              </div>
              <div className="text-3xl font-bold text-white mb-2">
                ₹{data.base_incentive.toLocaleString()}
              </div>
              <div className={clsx('text-xs font-medium', getSlab(data.achievement_percentage).color)}>
                {getSlab(data.achievement_percentage).label}
              </div>
            </div>

            {/* Kicker Bonus */}
            <div className="bg-gradient-to-br from-gray-800 to-gray-900 rounded-lg p-6 border border-gray-700">
              <div className="flex items-center justify-between mb-4">
                <span className="text-gray-400 text-sm font-medium">Kicker Bonus</span>
                <Zap className="w-5 h-5 text-yellow-400" />
              </div>
              <div className="text-3xl font-bold text-white mb-2">
                ₹{data.kicker_bonus.toLocaleString()}
              </div>
              <div className="text-xs text-gray-400">
                {data.kicker_count} kickers {data.kicker_count >= 3 ? '✓' : '(need 3+)'}
              </div>
            </div>

            {/* Total Incentive */}
            <div className="bg-gradient-to-br from-yellow-900/30 to-gray-900 rounded-lg p-6 border border-yellow-700/50">
              <div className="flex items-center justify-between mb-4">
                <span className="text-gray-400 text-sm font-medium">Total Incentive</span>
                <Award className="w-5 h-5 text-yellow-400" />
              </div>
              <div className="text-3xl font-bold text-yellow-400 mb-2">
                ₹{data.total_incentive.toLocaleString()}
              </div>
              <div className={clsx(
                'text-xs font-medium px-2 py-1 rounded w-fit',
                data.status === 'Below Target' ? 'bg-red-900/30 text-red-300' :
                data.status === 'Qualified' ? 'bg-blue-900/30 text-blue-300' :
                'bg-green-900/30 text-green-300'
              )}>
                {data.status}
              </div>
            </div>
          </div>

          {/* Sales Target Progress Bar with Slabs */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-white font-semibold mb-4 flex items-center gap-2">
              <Target className="w-5 h-5 text-blue-400" />
              Sales Target Progress
            </h3>

            {/* Main progress bar */}
            <div className="space-y-4">
              <div className="relative">
                <div className="w-full bg-gray-700 rounded-full h-4 overflow-hidden">
                  <div
                    className="h-4 bg-gradient-to-r from-blue-500 via-green-500 to-yellow-500 transition-all rounded-full"
                    style={{ width: `${Math.min(data.achievement_percentage, 150)}%` }}
                  />
                </div>

                {/* Slab Indicators */}
                <div className="flex justify-between text-xs font-medium mt-2 px-1">
                  <div className="text-center">
                    <div className="h-1 w-px bg-blue-400 mx-auto mb-1" />
                    <span className="text-blue-400">80%</span>
                    <div className="text-gray-400 text-[10px]">0.8%</div>
                  </div>
                  <div className="text-center">
                    <div className="h-1 w-px bg-green-400 mx-auto mb-1" />
                    <span className="text-green-400">100%</span>
                    <div className="text-gray-400 text-[10px]">1%</div>
                  </div>
                  <div className="text-center">
                    <div className="h-1 w-px bg-yellow-400 mx-auto mb-1" />
                    <span className="text-yellow-400">120%</span>
                    <div className="text-gray-400 text-[10px]">1.5%</div>
                  </div>
                </div>
              </div>

              {/* Next Milestone */}
              {data.next_slab && (
                <div className="bg-gray-900 rounded p-3 text-sm">
                  <div className="text-gray-400 mb-1">Current: {data.next_slab.current_slab}</div>
                  <div className="text-yellow-400 font-medium">Next: {data.next_slab.next_milestone}</div>
                </div>
              )}
            </div>
          </div>

          {/* Kicker Tracker */}
          {kickerData && (
            <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
              <h3 className="text-white font-semibold mb-4 flex items-center gap-2">
                <Zap className="w-5 h-5 text-yellow-400" />
                Kicker Sales ({monthName} {selectedYear})
              </h3>

              <div className="space-y-4">
                {/* Kicker Summary */}
                <div className="grid grid-cols-3 gap-3 mb-4">
                  <div className="bg-gray-900 rounded p-3 text-center">
                    <div className="text-2xl font-bold text-yellow-400">{kickerData.total_kickers}</div>
                    <div className="text-xs text-gray-400">Total Kickers</div>
                  </div>
                  <div className="bg-gray-900 rounded p-3 text-center">
                    <div className="text-2xl font-bold text-green-400">
                      {kickerData.total_kickers >= 3 ? '✓' : kickerData.total_kickers}
                    </div>
                    <div className="text-xs text-gray-400">Status</div>
                  </div>
                  <div className="bg-gray-900 rounded p-3 text-center">
                    <div className="text-xl font-bold text-blue-400">₹{kickerData.kicker_bonus}</div>
                    <div className="text-xs text-gray-400">Bonus</div>
                  </div>
                </div>

                {/* Brand Summary */}
                {Object.keys(kickerData.brand_summary).length > 0 && (
                  <div>
                    <div className="text-sm text-gray-400 mb-2">By Brand:</div>
                    <div className="space-y-2">
                      {Object.entries(kickerData.brand_summary).map(([brand, count]) => (
                        <div key={brand} className="flex items-center justify-between text-sm bg-gray-900 rounded p-2">
                          <span className="text-gray-300">{brand}</span>
                          <span className="text-yellow-400 font-semibold">{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Recent Kickers Table */}
                {kickerData.kickers.length > 0 && (
                  <div className="mt-4">
                    <div className="text-sm text-gray-400 mb-2">Recent Sales:</div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-gray-700">
                            <th className="text-left py-2 px-2 text-gray-400">Brand</th>
                            <th className="text-right py-2 px-2 text-gray-400">Amount</th>
                            <th className="text-right py-2 px-2 text-gray-400">Date</th>
                          </tr>
                        </thead>
                        <tbody>
                          {kickerData.kickers.slice(0, 5).map((k) => (
                            <tr key={k.kicker_id} className="border-b border-gray-700 hover:bg-gray-900">
                              <td className="py-2 px-2 text-gray-300 text-xs">{k.brand}</td>
                              <td className="py-2 px-2 text-right text-yellow-400 font-semibold">₹{k.sale_amount}</td>
                              <td className="py-2 px-2 text-right text-gray-400 text-xs">
                                {new Date(k.sale_date).toLocaleDateString()}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Incentive Breakdown */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-white font-semibold mb-4 flex items-center gap-2">
              <Medal className="w-5 h-5 text-indigo-400" />
              Incentive Calculation Breakdown
            </h3>

            <div className="space-y-3">
              {/* Base Incentive */}
              <div className="bg-gray-900 rounded p-4">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <div className="text-gray-400 text-sm">Sales-Based Incentive</div>
                    <div className="text-gray-300 text-xs mt-1">
                      ₹{data.actual_sales.toLocaleString()} × {getSlab(data.achievement_percentage).label.split(' ')[0]}
                    </div>
                  </div>
                  <div className="text-lg font-bold text-green-400">₹{data.base_incentive.toLocaleString()}</div>
                </div>
              </div>

              {/* Kicker Bonus */}
              <div className="bg-gray-900 rounded p-4">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <div className="text-gray-400 text-sm">Kicker Bonus</div>
                    <div className="text-gray-300 text-xs mt-1">
                      {data.kicker_count} × ₹200 {data.kicker_count < 3 && '(need 3+ to qualify)'}
                    </div>
                  </div>
                  <div className="text-lg font-bold text-yellow-400">₹{data.kicker_bonus.toLocaleString()}</div>
                </div>
              </div>

              {/* Google Review Bonus */}
              {data.google_reviews > 0 && (
                <div className="bg-gray-900 rounded p-4">
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <div className="text-gray-400 text-sm">Google Review Bonus</div>
                      <div className="text-gray-300 text-xs mt-1">
                        {data.google_reviews} reviews @ ₹25-50 each
                      </div>
                    </div>
                    <div className="text-lg font-bold text-blue-400">₹{data.google_review_bonus.toLocaleString()}</div>
                  </div>
                </div>
              )}

              {/* Total */}
              <div className="bg-yellow-900/30 border border-yellow-700/50 rounded p-4">
                <div className="flex justify-between items-center">
                  <div className="text-white font-semibold">Total Incentive</div>
                  <div className="text-2xl font-bold text-yellow-400">₹{data.total_incentive.toLocaleString()}</div>
                </div>
              </div>
            </div>
          </div>

          {/* Staff Leaderboard */}
          {leaderboard.length > 0 && (
            <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
              <h3 className="text-white font-semibold mb-4 flex items-center gap-2">
                <Medal className="w-5 h-5 text-yellow-400" />
                Staff Leaderboard ({monthName} {selectedYear})
              </h3>

              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-gray-700">
                      <th className="text-left py-3 px-4 text-gray-400 text-sm font-medium">Rank</th>
                      <th className="text-left py-3 px-4 text-gray-400 text-sm font-medium">Name</th>
                      <th className="text-right py-3 px-4 text-gray-400 text-sm font-medium">Achievement</th>
                      <th className="text-right py-3 px-4 text-gray-400 text-sm font-medium">Sales</th>
                      <th className="text-right py-3 px-4 text-gray-400 text-sm font-medium">Incentive</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaderboard.slice(0, 10).map((entry, idx) => (
                      <tr
                        key={entry.staff_id}
                        className={clsx(
                          'border-b border-gray-700 hover:bg-gray-900/50 transition-colors',
                          idx === 0 ? 'bg-yellow-900/20' : idx === 1 ? 'bg-gray-700/20' : idx === 2 ? 'bg-orange-900/20' : ''
                        )}
                      >
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2">
                            {idx === 0 && <span className="text-xl">🥇</span>}
                            {idx === 1 && <span className="text-xl">🥈</span>}
                            {idx === 2 && <span className="text-xl">🥉</span>}
                            <span className="text-gray-300 font-semibold">{entry.rank}</span>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-gray-300">{entry.staff_name}</td>
                        <td className="py-3 px-4 text-right">
                          <div className="flex flex-col items-end gap-1">
                            <span className={clsx(
                              'font-bold',
                              entry.achievement_percentage >= 120 ? 'text-yellow-400' :
                              entry.achievement_percentage >= 100 ? 'text-green-400' :
                              'text-blue-400'
                            )}>
                              {entry.achievement_percentage.toFixed(1)}%
                            </span>
                            <span className="text-xs text-gray-400">
                              ₹{(entry.actual_sales / 100000).toFixed(1)}L
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-right text-gray-300">
                          ₹{(entry.actual_sales / 100000).toFixed(1)}L
                        </td>
                        <td className="py-3 px-4 text-right">
                          <span className="font-bold text-yellow-400">
                            ₹{(entry.total_incentive / 1000).toFixed(0)}K
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {leaderboard.length > 10 && (
                <div className="mt-4 text-center text-sm text-gray-400">
                  Showing top 10 of {leaderboard.length} staff members
                </div>
              )}
            </div>
          )}
        </>
      ) : (
        <div className="bg-gray-800 rounded-lg p-12 text-center border border-gray-700">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-gray-500" />
          <p className="text-gray-400">No incentive data available for this period</p>
        </div>
      )}
    </div>
  );
}

export default IncentiveDashboard;

// ============================================================================
// Commission Leaderboard (HR-3)
// ============================================================================
// Staff sales leaderboard + per-staff commission summary for managers.
// All authenticated users see ranks; names are revealed only to managers
// (backend enforces the same rule -- non-managers see own row with name,
// others are anonymised as "Staff Member").

import { useState, useEffect } from 'react';
import { Trophy, TrendingUp, Loader2 } from 'lucide-react';
import { payrollApi } from '../../services/api/payroll';
import type { CommissionSummary, LeaderboardEntry } from '../../services/api/payroll';
import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';
import clsx from 'clsx';

type Period = 'today' | 'week' | 'month';

const BADGE_STYLES: Record<string, string> = {
  Champion: 'bg-yellow-100 text-yellow-700',
  'Star Performer': 'bg-blue-100 text-blue-700',
  'Rising Star': 'bg-green-100 text-green-700',
  'Team Player': 'bg-gray-100 text-gray-600',
};

export function CommissionLeaderboard({ storeId }: { storeId?: string }) {
  const { user } = useAuth();
  const [period, setPeriod] = useState<Period>('month');
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [commissionData, setCommissionData] = useState<CommissionSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const now = new Date();

  useEffect(() => {
    loadData();
  }, [period, storeId]);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [lb, cs] = await Promise.all([
        payrollApi.getCommissionLeaderboard({ period, store_id: storeId }).catch(() => null),
        payrollApi.getCommissionSummary({
          month: now.getMonth() + 1,
          year: now.getFullYear(),
          store_id: storeId,
        }).catch(() => null),
      ]);
      setLeaderboard(lb?.leaderboard || []);
      setCommissionData(cs);
    } catch {
      // fail soft
    } finally {
      setIsLoading(false);
    }
  };

  const _managerRoles: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];
  const canSeeCommission = _managerRoles.some((r) => (user?.roles || []).includes(r));

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
          <Trophy className="w-5 h-5 text-yellow-500" />
          Sales Leaderboard
        </h2>
        <div className="flex gap-1 bg-gray-100 rounded-lg p-0.5">
          {(['today', 'week', 'month'] as Period[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPeriod(p)}
              className={clsx(
                'px-3 py-1 rounded-md text-xs font-medium transition-colors capitalize',
                period === p
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              )}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
        </div>
      ) : leaderboard.length === 0 ? (
        <div className="text-center py-8 text-sm text-gray-400">
          No sales recorded for this period.
        </div>
      ) : (
        <div className="space-y-2">
          {leaderboard.map((entry) => (
            <div
              key={entry.staff_id}
              className={clsx(
                'flex items-center gap-3 p-3 rounded-lg border transition-colors',
                entry.is_self
                  ? 'border-bv-red-200 bg-bv-red-50'
                  : 'border-gray-200 bg-white'
              )}
            >
              {/* Rank */}
              <div className="w-8 h-8 flex items-center justify-center font-bold text-sm shrink-0">
                {entry.rank === 1 && (
                  <span className="text-yellow-500 text-lg">1</span>
                )}
                {entry.rank === 2 && (
                  <span className="text-gray-400 text-base">2</span>
                )}
                {entry.rank === 3 && (
                  <span className="text-orange-400 text-base">3</span>
                )}
                {entry.rank > 3 && (
                  <span className="text-gray-400 text-sm">{entry.rank}</span>
                )}
              </div>

              {/* Name + badge */}
              <div className="flex-1 min-w-0">
                <p className="font-medium text-gray-900 text-sm truncate">
                  {entry.name}
                  {entry.is_self && (
                    <span className="ml-1 text-xs text-bv-red-600">(you)</span>
                  )}
                </p>
                <span
                  className={clsx(
                    'text-xs px-1.5 py-0.5 rounded font-medium',
                    BADGE_STYLES[entry.badge] || BADGE_STYLES['Team Player']
                  )}
                >
                  {entry.badge}
                </span>
              </div>

              {/* Stats */}
              <div className="text-right shrink-0">
                <p className="text-sm font-bold text-gray-900">
                  Rs {entry.revenue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                </p>
                <p className="text-xs text-gray-400">{entry.sales_count} sales</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Commission summary (manager only) */}
      {canSeeCommission && commissionData && commissionData.items.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2 mb-3">
            <TrendingUp className="w-4 h-4" />
            Commission This Month
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-1.5 text-gray-500 font-medium">Staff</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Sales</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Revenue</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Rate</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Commission</th>
                </tr>
              </thead>
              <tbody>
                {commissionData.items.map((item) => (
                  <tr key={item.employee_id} className="border-b border-gray-100">
                    <td className="py-1.5 text-gray-900 font-medium">{item.name}</td>
                    <td className="py-1.5 text-right text-gray-600">{item.sales_count}</td>
                    <td className="py-1.5 text-right text-gray-600">
                      Rs {item.revenue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </td>
                    <td className="py-1.5 text-right text-gray-400">
                      {item.commission_rate_percent}%
                    </td>
                    <td className="py-1.5 text-right font-semibold text-green-700">
                      {item.commission_amount > 0
                        ? `Rs ${item.commission_amount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
                        : '--'}
                    </td>
                  </tr>
                ))}
              </tbody>
              {commissionData.total_commission > 0 && (
                <tfoot>
                  <tr className="border-t border-gray-200">
                    <td colSpan={4} className="py-1.5 text-gray-600 font-medium">Total</td>
                    <td className="py-1.5 text-right font-bold text-green-700">
                      Rs {commissionData.total_commission.toLocaleString('en-IN', {
                        maximumFractionDigits: 2,
                      })}
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

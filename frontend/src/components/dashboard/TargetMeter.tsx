// ============================================================================
// IMS 2.0 - Target vs Achievement Meter Component
// ============================================================================
// Visual progress indicator for sales targets vs actual achievement

import { IndianRupee, TrendingUp } from 'lucide-react';
import clsx from 'clsx';

interface TargetMeterProps {
  actual: number;
  target: number;
  label: string;
  period: 'daily' | 'monthly';
  loading?: boolean;
  error?: boolean;
}

export function TargetMeter({
  actual,
  target,
  label,
  period,
  loading = false,
  error = false,
}: TargetMeterProps) {
  // Calculate percentage
  const percentage = target > 0 ? (actual / target) * 100 : 0;
  const cappedPercentage = Math.min(percentage, 100);
  
  // Determine color based on achievement
  const getColor = (): { bg: string; text: string; icon: string } => {
    if (percentage < 50) {
      return { bg: 'bg-red-600', text: 'text-red-600', icon: 'text-red-500' };
    } else if (percentage < 80) {
      return { bg: 'bg-amber-600', text: 'text-amber-600', icon: 'text-amber-500' };
    } else if (percentage < 100) {
      return { bg: 'bg-green-600', text: 'text-green-600', icon: 'text-green-500' };
    } else {
      return { bg: 'bg-blue-600', text: 'text-blue-600', icon: 'text-blue-500' };
    }
  };

  const color = getColor();
  const isOverTarget = percentage > 100;
  const displayPercentage = Math.min(percentage, 100);

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  if (loading) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 shadow-sm">
        <div className="mb-3 h-4 bg-gray-700 rounded animate-pulse w-1/3" />
        <div className="h-8 bg-gray-700 rounded animate-pulse mb-3" />
        <div className="h-3 bg-gray-700 rounded animate-pulse" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 border border-red-500 shadow-sm">
        <p className="text-xs text-red-600 font-medium">Error loading target data</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 shadow-sm">
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">
            {label}
          </p>
          <p className="text-xs text-gray-500 mt-1">
            {period === 'daily' ? 'Daily Target' : 'Monthly Target'}
          </p>
        </div>
        {isOverTarget && (
          <div className="flex items-center gap-1 bg-blue-50 px-2 py-1 rounded-lg">
            <TrendingUp className="w-3.5 h-3.5 text-blue-600" />
            <span className="text-xs font-semibold text-blue-600">
              +{(percentage - 100).toFixed(0)}%
            </span>
          </div>
        )}
      </div>

      {/* Amount Display */}
      <div className="mb-3">
        <div className="flex items-baseline gap-1 mb-1">
          <span className="text-xl font-bold text-white">
            {formatCurrency(actual)}
          </span>
          <span className="text-xs text-gray-400">
            / {formatCurrency(target)}
          </span>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="mb-3">
        <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
          <div
            className={clsx('h-full rounded-full transition-all duration-300', color.bg)}
            style={{ width: `${displayPercentage}%` }}
          />
        </div>
      </div>

      {/* Percentage and Status */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={clsx('text-lg font-bold', color.text)}>
            {percentage.toFixed(1)}%
          </span>
          <span className="text-xs text-gray-400">
            {percentage < 50 && 'Below Target'}
            {percentage >= 50 && percentage < 80 && 'On Track'}
            {percentage >= 80 && percentage < 100 && 'Almost There'}
            {isOverTarget && 'Target Met!'}
          </span>
        </div>
      </div>

      {/* Gap Information */}
      {percentage < 100 && (
        <div className="mt-2 pt-2 border-t border-gray-700">
          <p className="text-xs text-gray-400">
            <span className="text-red-600 font-medium">
              ₹{formatCurrency(Math.max(0, target - actual))}
            </span>
            {' '}remaining
          </p>
        </div>
      )}
    </div>
  );
}

export default TargetMeter;

import { useMemo } from 'react';
import { TrendingUp, TrendingDown, Target, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

// ============================================================================
// Sparkline Chart Component
// ============================================================================

interface SparklineProps {
  data: number[];
  height?: number;
  strokeColor?: string;
  fillColor?: string;
}

function Sparkline({ data, height = 24, strokeColor = '#3b82f6', fillColor = '#dbeafe' }: SparklineProps) {
  if (data.length < 2) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const width = 60;
  const padding = 2;

  const pathData = `M ${data.map((value, i) => {
    const x = padding + (i / (data.length - 1)) * (width - padding * 2);
    const y = height - padding - ((value - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  }).join(' L ')}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="mx-auto">
      <defs>
        <linearGradient id="sparklineGradient" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor={fillColor} stopOpacity="0.6" />
          <stop offset="100%" stopColor={fillColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* Area fill */}
      <path
        d={`${pathData} L ${width - padding},${height} L ${padding},${height} Z`}
        fill="url(#sparklineGradient)"
      />
      {/* Line */}
      <path d={pathData} stroke={strokeColor} strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      {/* Last point dot */}
      <circle
        cx={padding + ((data.length - 1) / (data.length - 1)) * (width - padding * 2)}
        cy={height - padding - ((data[data.length - 1] - min) / range) * (height - padding * 2)}
        r="1.5"
        fill={strokeColor}
      />
    </svg>
  );
}

// ============================================================================
// Enterprise KPI Card Component
// ============================================================================

export interface EnterpriseKpiCardProps {
  label: string;
  value: string | number;
  subtext?: string;
  change?: number;
  target?: number;
  unit?: string;
  icon: React.ComponentType<{ className?: string }>;
  sparklineData?: number[];
  status?: 'positive' | 'negative' | 'neutral' | 'warning' | 'success';
  loading?: boolean;
  error?: boolean;
  onClick?: () => void;
  trend?: 'up' | 'down' | 'stable';
  comparison?: {
    label: string;
    value: number;
  };
}

export function EnterpriseKpiCard({
  label,
  value,
  subtext,
  change,
  target,
  unit,
  icon: Icon,
  sparklineData,
  status = 'neutral',
  loading,
  error,
  onClick,
  comparison,
}: EnterpriseKpiCardProps) {
  // Determine status color
  const statusColors = {
    positive: { bg: 'bg-green-50', border: 'border-green-200', icon: 'text-green-600', text: 'text-green-700', badge: 'bg-green-100' },
    negative: { bg: 'bg-red-50', border: 'border-red-200', icon: 'text-red-600', text: 'text-red-700', badge: 'bg-red-100' },
    neutral: { bg: 'bg-blue-50', border: 'border-blue-200', icon: 'text-blue-600', text: 'text-blue-700', badge: 'bg-blue-100' },
    warning: { bg: 'bg-amber-50', border: 'border-amber-200', icon: 'text-amber-600', text: 'text-amber-700', badge: 'bg-amber-100' },
    success: { bg: 'bg-emerald-50', border: 'border-emerald-200', icon: 'text-emerald-600', text: 'text-emerald-700', badge: 'bg-emerald-100' },
  };

  const colors = statusColors[status];
  const performancePercent = target ? Math.min(100, (Number(value) / target) * 100) : null;

  // Determine sparkline color based on trend
  const sparklineColor = useMemo(() => {
    if (!sparklineData || sparklineData.length < 2) return '#3b82f6';
    const lastValue = sparklineData[sparklineData.length - 1];
    const firstValue = sparklineData[0];
    if (lastValue > firstValue) return '#10b981';
    if (lastValue < firstValue) return '#ef4444';
    return '#3b82f6';
  }, [sparklineData]);

  return (
    <div
      className={clsx(
        'rounded-xl border-2 p-4 transition-all',
        error ? 'border-red-300 bg-red-50' : clsx(colors.border, colors.bg),
        onClick && 'cursor-pointer hover:shadow-md',
      )}
      onClick={onClick}
    >
      {/* Header: Icon & Status Badge */}
      <div className="flex items-start justify-between mb-3">
        <div className={clsx('w-10 h-10 rounded-lg flex items-center justify-center', error ? 'bg-red-100' : colors.badge)}>
          {error ? (
            <AlertCircle className="w-5 h-5 text-red-600" />
          ) : (
            <Icon className={clsx('w-5 h-5', error ? 'text-red-600' : colors.icon)} />
          )}
        </div>

        {/* Status Indicator */}
        {!error && (
          <div className="flex items-center gap-1">
            {status === 'positive' && <TrendingUp className={clsx('w-4 h-4', colors.icon)} />}
            {status === 'negative' && <TrendingDown className={clsx('w-4 h-4', colors.icon)} />}
            {status === 'warning' && <AlertCircle className={clsx('w-4 h-4', colors.icon)} />}
          </div>
        )}
      </div>

      {/* Main Value */}
      {loading ? (
        <div className="h-8 w-24 bg-gray-200 rounded animate-pulse mb-2" />
      ) : error ? (
        <p className="text-sm font-medium text-red-700">Data unavailable</p>
      ) : (
        <>
          <div className="flex items-baseline gap-2 mb-1">
            <p className="text-3xl font-bold text-gray-900">{value}</p>
            {unit && <p className="text-sm text-gray-600">{unit}</p>}
          </div>

          {/* Label */}
          <p className="text-xs font-medium text-gray-600 uppercase tracking-wide mb-3">{label}</p>

          {/* Target Progress Bar */}
          {target && performancePercent !== null && (
            <div className="mb-3">
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs text-gray-600">Target: {target}</span>
                <span className="text-xs font-semibold text-gray-700">{performancePercent.toFixed(0)}%</span>
              </div>
              <div className="w-full h-1.5 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className={clsx(
                    'h-full rounded-full transition-all',
                    performancePercent >= 100 ? 'bg-green-500' :
                    performancePercent >= 80 ? 'bg-blue-500' :
                    'bg-amber-500'
                  )}
                  style={{ width: `${Math.min(100, performancePercent)}%` }}
                />
              </div>
            </div>
          )}

          {/* Change % */}
          {change !== undefined && (
            <div className="flex items-center gap-1 mb-3">
              {change > 0 && <TrendingUp className="w-3 h-3 text-green-600" />}
              {change < 0 && <TrendingDown className="w-3 h-3 text-red-600" />}
              <span className={clsx(
                'text-xs font-semibold',
                change > 0 ? 'text-green-600' : change < 0 ? 'text-red-600' : 'text-gray-600'
              )}>
                {change > 0 ? '+' : ''}{change}% {comparison?.label || 'vs previous'}
              </span>
            </div>
          )}

          {/* Subtext */}
          {subtext && (
            <p className="text-xs text-gray-600 mb-3">{subtext}</p>
          )}

          {/* Sparkline Chart */}
          {sparklineData && sparklineData.length > 1 && (
            <div className="flex justify-center py-2 border-t border-gray-200 pt-2">
              <Sparkline
                data={sparklineData}
                height={24}
                strokeColor={sparklineColor}
                fillColor={sparklineColor}
              />
            </div>
          )}
        </>
      )}

      {/* Target Indicator */}
      {!loading && target && (
        <div className="mt-2 flex items-center gap-1 text-xs text-gray-600">
          <Target className="w-3 h-3" />
          Target: {target}
        </div>
      )}
    </div>
  );
}

export default EnterpriseKpiCard;

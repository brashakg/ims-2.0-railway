// ============================================================================
// IMS 2.0 - Dashboard Chart Components
// ============================================================================
// Reusable chart components for data visualization

import { useMemo } from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';
import clsx from 'clsx';

export interface ChartDataPoint {
  name: string;
  value: number;
  label?: string;
}

interface LineChartProps {
  data: ChartDataPoint[];
  title: string;
  color?: string;
  height?: number;
}

interface BarChartProps {
  data: ChartDataPoint[];
  title: string;
  color?: string;
  height?: number;
}

interface PieChartProps {
  data: ChartDataPoint[];
  title: string;
  height?: number;
}

interface KPICardProps {
  title: string;
  value: number | string;
  unit?: string;
  trend?: number; // percentage change
  icon?: React.ReactNode;
  color?: 'red' | 'green' | 'blue' | 'amber' | 'purple';
}

// Simple SVG-based Line Chart
export function LineChart({ data, title, color = '#dc2626', height = 200 }: LineChartProps) {
  const maxValue = useMemo(() => Math.max(...data.map(d => d.value)), [data]);
  const minValue = useMemo(() => Math.min(...data.map(d => d.value)), [data]);
  const range = maxValue - minValue || maxValue;

  const points = useMemo(() => {
    const width = 600;
    const padding = 40;
    const chartWidth = width - padding * 2;
    const chartHeight = height - padding * 2;

    return data.map((d, i) => {
      const x = padding + (i / (data.length - 1 || 1)) * chartWidth;
      const y = height - padding - ((d.value - minValue) / range) * chartHeight;
      return { x, y, ...d };
    });
  }, [data, height]);

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-medium text-gray-900 mb-4">{title}</h3>
      <svg width="100%" height={height} viewBox={`0 0 600 ${height}`} className="w-full">
        {/* Grid lines */}
        {[0, 0.25, 0.5, 0.75, 1].map((ratio, i) => (
          <line
            key={i}
            x1="40"
            y1={height - 40 - ratio * (height - 80)}
            x2="560"
            y2={height - 40 - ratio * (height - 80)}
            stroke="#e5e7eb"
            strokeDasharray="4"
          />
        ))}

        {/* Line */}
        <path d={pathD} fill="none" stroke={color} strokeWidth="2" />

        {/* Points */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r="4"
            fill={color}
            opacity="0.5"
            style={{ transition: 'all 0.2s ease' }}
          />
        ))}
      </svg>
      <div className="mt-4 text-xs text-gray-500 text-center">
        {data.length} data points
      </div>
    </div>
  );
}

// Simple SVG-based Bar Chart
export function BarChart({ data, title, color = '#2563eb', height = 200 }: BarChartProps) {
  const maxValue = useMemo(() => Math.max(...data.map(d => d.value)), [data]);
  const barWidth = useMemo(() => Math.max(20, 600 / (data.length * 2)), [data]);

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-medium text-gray-900 mb-4">{title}</h3>
      <svg width="100%" height={height} viewBox={`0 0 600 ${height}`} className="w-full">
        {/* Y-axis labels */}
        {[0, 0.25, 0.5, 0.75, 1].map((ratio, i) => (
          <g key={i}>
            <line x1="35" y1={height - 40 - ratio * (height - 80)} x2="40" y2={height - 40 - ratio * (height - 80)} stroke="#d1d5db" />
            <text x="30" y={height - 36 - ratio * (height - 80)} fontSize="10" fill="#6b7280" textAnchor="end">
              {Math.round(ratio * maxValue)}
            </text>
          </g>
        ))}

        {/* Bars */}
        {data.map((d, i) => {
          const barHeight = ((d.value / maxValue) || 0) * (height - 80);
          const x = 50 + i * (550 / data.length) + (550 / data.length - barWidth) / 2;
          const y = height - 40 - barHeight;

          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={barHeight}
                fill={color}
                opacity="0.8"
                rx="4"
                className="hover:opacity-100 transition-opacity cursor-pointer"
              />
              <text
                x={x + barWidth / 2}
                y={height - 20}
                fontSize="10"
                fill="#6b7280"
                textAnchor="middle"
                className="truncate"
              >
                {d.name.substring(0, 3)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// Simple SVG-based Pie Chart
export function PieChart({ data, title, height = 200 }: PieChartProps) {
  const total = useMemo(() => data.reduce((sum, d) => sum + d.value, 0), [data]);
  const colors = ['#dc2626', '#2563eb', '#16a34a', '#d97706', '#8b5cf6', '#ec4899'];

  let currentAngle = -Math.PI / 2;
  const slices = data.map((d, i) => {
    const sliceAngle = (d.value / total) * 2 * Math.PI;
    const slice = {
      ...d,
      startAngle: currentAngle,
      endAngle: currentAngle + sliceAngle,
      color: colors[i % colors.length],
    };
    currentAngle += sliceAngle;
    return slice;
  });

  const center = 100;
  const radius = 80;

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-medium text-gray-900 mb-4">{title}</h3>
      <div className="flex items-center justify-between">
        <svg width="150" height={height} viewBox={`0 0 200 ${height}`} className="flex-shrink-0">
          {slices.map((slice, i) => {
            const x1 = center + radius * Math.cos(slice.startAngle);
            const y1 = center + radius * Math.sin(slice.startAngle);
            const x2 = center + radius * Math.cos(slice.endAngle);
            const y2 = center + radius * Math.sin(slice.endAngle);
            const largeArc = slice.endAngle - slice.startAngle > Math.PI ? 1 : 0;

            const pathData = `M ${center} ${center} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;

            return (
              <path
                key={i}
                d={pathData}
                fill={slice.color}
                opacity="0.8"
                className="hover:opacity-100 transition-opacity cursor-pointer"
              />
            );
          })}
        </svg>

        <div className="flex-1 ml-4 space-y-2">
          {slices.map((slice, i) => (
            <div key={i} className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2">
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: slice.color }}
                />
                <span className="text-gray-700">{slice.name}</span>
              </div>
              <span className="font-medium text-gray-900">
                {((slice.value / total) * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// KPI Card with Trend Indicator
export function KPICard({
  title,
  value,
  unit,
  trend,
  icon,
  color = 'blue',
}: KPICardProps) {
  const colorClasses = {
    red: 'bg-red-50 text-red-700 border-red-200',
    green: 'bg-green-50 text-green-700 border-green-200',
    blue: 'bg-blue-50 text-blue-700 border-blue-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    purple: 'bg-purple-50 text-purple-700 border-purple-200',
  };

  const trendColor = trend && trend >= 0 ? 'text-green-600' : 'text-red-600';
  const TrendIcon = trend && trend >= 0 ? TrendingUp : TrendingDown;

  return (
    <div className={clsx('rounded-lg border p-4', colorClasses[color])}>
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <p className="text-sm font-medium opacity-75">{title}</p>
          <div className="flex items-baseline gap-2 mt-2">
            <p className="text-2xl font-bold">{value}</p>
            {unit && <p className="text-sm opacity-75">{unit}</p>}
          </div>
        </div>
        {icon && <div className="text-2xl opacity-50">{icon}</div>}
      </div>

      {trend !== undefined && (
        <div className={clsx('flex items-center gap-1 mt-3 text-sm font-medium', trendColor)}>
          <TrendIcon className="w-4 h-4" />
          <span>{Math.abs(trend)}% {trend >= 0 ? 'increase' : 'decrease'}</span>
        </div>
      )}
    </div>
  );
}

// Data Table with Sorting
export interface TableColumn {
  key: string;
  label: string;
  width?: string;
  format?: (value: any) => string;
}

interface DataTableProps {
  columns: TableColumn[];
  data: Record<string, any>[];
  title?: string;
  maxRows?: number;
  loading?: boolean;
}

export function DataTable({
  columns,
  data,
  title,
  maxRows = 10,
  loading = false,
}: DataTableProps) {
  const displayData = data.slice(0, maxRows);

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        {title && <h3 className="text-sm font-medium text-gray-900 mb-4">{title}</h3>}
        <div className="animate-pulse space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-8 bg-gray-100 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      {title && <div className="px-4 py-3 border-b border-gray-200">
        <h3 className="text-sm font-medium text-gray-900">{title}</h3>
      </div>}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {columns.map(col => (
                <th
                  key={col.key}
                  className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider"
                  style={{ width: col.width }}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {displayData.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-4 py-8 text-center text-gray-500">
                  No data available
                </td>
              </tr>
            ) : (
              displayData.map((row, i) => (
                <tr key={i} className="hover:bg-gray-50 transition-colors">
                  {columns.map(col => (
                    <td key={col.key} className="px-4 py-3 text-sm text-gray-900">
                      {col.format ? col.format(row[col.key]) : row[col.key]}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {data.length > maxRows && (
        <div className="px-4 py-3 text-xs text-gray-500 border-t border-gray-200 bg-gray-50">
          Showing {displayData.length} of {data.length} rows
        </div>
      )}
    </div>
  );
}

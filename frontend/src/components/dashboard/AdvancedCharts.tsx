import clsx from 'clsx';

// ============================================================================
// Line Chart with Area Fill
// ============================================================================

export interface LineChartDataPoint {
  label: string;
  value: number;
  value2?: number; // For YoY comparison
}

interface LineChartProps {
  data: LineChartDataPoint[];
  height?: number;
  showLegend?: boolean;
  showGrid?: boolean;
  color?: string;
  color2?: string;
  loading?: boolean;
}

export function LineChart({
  data,
  height = 250,
  showLegend = true,
  showGrid = true,
  color = '#3b82f6',
  color2 = '#10b981',
  loading,
}: LineChartProps) {
  if (loading) {
    return (
      <div className={clsx('w-full rounded-lg bg-gray-50 animate-pulse', `h-[${height}px]`)} />
    );
  }

  if (!data || data.length < 2) {
    return (
      <div className="flex items-center justify-center h-64 bg-gray-50 rounded-lg">
        <p className="text-gray-500">No data available</p>
      </div>
    );
  }

  const max = Math.max(...data.map(d => Math.max(d.value, d.value2 || 0)));
  const min = Math.min(...data.map(d => Math.min(d.value, d.value2 || d.value)));
  const range = max - min || max || 1;

  const padding = { top: 30, right: 20, bottom: 40, left: 60 };
  const chartHeight = height - padding.top - padding.bottom;

  // SVG dimensions
  const svgWidth = 600;
  const svgHeight = height;

  // Generate path data
  const generatePath = (values: number[]) => {
    return values
      .map((value, i) => {
        const x = padding.left + (i / (values.length - 1)) * (svgWidth - padding.left - padding.right);
        const y = padding.top + ((max - value) / range) * chartHeight;
        return `${x},${y}`;
      })
      .join(' L ');
  };

  const primaryValues = data.map(d => d.value);
  const secondaryValues = data.map(d => d.value2 || 0).some(v => v > 0) ? data.map(d => d.value2 || 0) : null;

  const primaryPathStr = generatePath(primaryValues);
  const secondaryPathStr = secondaryValues ? generatePath(secondaryValues) : null;

  return (
    <div className="w-full h-full">
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        preserveAspectRatio="xMidYMid meet"
        className="rounded-lg"
      >
        <defs>
          {/* Primary gradient */}
          <linearGradient id="primaryGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
          {/* Secondary gradient */}
          {secondaryPathStr && (
            <linearGradient id="secondaryGradient" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor={color2} stopOpacity="0.2" />
              <stop offset="100%" stopColor={color2} stopOpacity="0" />
            </linearGradient>
          )}
        </defs>

        {/* Grid lines */}
        {showGrid && (
          <>
            {[0, 1, 2, 3, 4].map((i) => (
              <line
                key={`gridline-${i}`}
                x1={padding.left}
                y1={padding.top + (i / 4) * chartHeight}
                x2={svgWidth - padding.right}
                y2={padding.top + (i / 4) * chartHeight}
                stroke="#e5e7eb"
                strokeDasharray="4,4"
                strokeWidth="1"
              />
            ))}
          </>
        )}

        {/* Y-Axis */}
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={svgHeight - padding.bottom} stroke="#d1d5db" strokeWidth="1" />

        {/* X-Axis */}
        <line x1={padding.left} y1={svgHeight - padding.bottom} x2={svgWidth - padding.right} y2={svgHeight - padding.bottom} stroke="#d1d5db" strokeWidth="1" />

        {/* Primary area fill */}
        <path
          d={`M ${primaryPathStr} L ${svgWidth - padding.right},${svgHeight - padding.bottom} L ${padding.left},${svgHeight - padding.bottom} Z`}
          fill="url(#primaryGradient)"
        />

        {/* Secondary area fill */}
        {secondaryPathStr && (
          <path
            d={`M ${secondaryPathStr} L ${svgWidth - padding.right},${svgHeight - padding.bottom} L ${padding.left},${svgHeight - padding.bottom} Z`}
            fill="url(#secondaryGradient)"
          />
        )}

        {/* Primary line */}
        <path d={`M ${primaryPathStr}`} stroke={color} strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {/* Secondary line */}
        {secondaryPathStr && (
          <path d={`M ${secondaryPathStr}`} stroke={color2} strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        )}

        {/* Data point dots */}
        {data.map((d, i) => {
          const x = padding.left + (i / (data.length - 1)) * (svgWidth - padding.left - padding.right);
          const y = padding.top + ((max - d.value) / range) * chartHeight;
          return (
            <circle key={`point-${i}`} cx={x} cy={y} r="3" fill={color} stroke="white" strokeWidth="1" />
          );
        })}

        {/* X-axis labels */}
        {data.map((d, i) => {
          if (i % Math.ceil(data.length / 5) !== 0 && i !== data.length - 1) return null;
          const x = padding.left + (i / (data.length - 1)) * (svgWidth - padding.left - padding.right);
          return (
            <text
              key={`label-${i}`}
              x={x}
              y={svgHeight - padding.bottom + 20}
              textAnchor="middle"
              className="text-xs fill-gray-600"
            >
              {d.label}
            </text>
          );
        })}

        {/* Y-axis labels */}
        {[0, 1, 2, 3, 4].map((i) => {
          const value = min + (i / 4) * range;
          return (
            <text
              key={`yaxis-${i}`}
              x={padding.left - 10}
              y={padding.top + (i / 4) * chartHeight + 4}
              textAnchor="end"
              className="text-xs fill-gray-600"
            >
              {formatChartValue(value)}
            </text>
          );
        })}
      </svg>

      {/* Legend */}
      {showLegend && (
        <div className="flex gap-4 justify-center mt-4">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
            <span className="text-sm text-gray-600">Current Period</span>
          </div>
          {secondaryPathStr && (
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color2 }} />
              <span className="text-sm text-gray-600">Previous Period / YoY</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Horizontal Bar Chart
// ============================================================================

interface BarChartData {
  label: string;
  value: number;
  value2?: number;
  color?: string;
}

interface BarChartProps {
  data: BarChartData[];
  height?: number;
  showValues?: boolean;
  loading?: boolean;
}

export function HorizontalBarChart({ data, height = 300, showValues = true, loading }: BarChartProps) {
  if (loading) {
    return <div className="w-full rounded-lg bg-gray-50 animate-pulse" style={{ height }} />;
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 bg-gray-50 rounded-lg">
        <p className="text-gray-500">No data available</p>
      </div>
    );
  }

  const max = Math.max(...data.map(d => Math.max(d.value, d.value2 || 0)));

  return (
    <div className="w-full">
      {data.map((item, i) => {
        const percent = (item.value / max) * 100;
        const percent2 = item.value2 ? (item.value2 / max) * 100 : 0;

        return (
          <div key={`bar-${i}`} className="mb-4">
            <div className="flex items-center justify-between mb-1">
              <label className="text-sm font-medium text-gray-700 max-w-[120px] truncate">{item.label}</label>
              {showValues && (
                <span className="text-sm font-semibold text-gray-900">{formatChartValue(item.value)}</span>
              )}
            </div>

            <div className="flex gap-1 h-6 rounded-lg overflow-hidden bg-gray-100">
              <div
                className={clsx('bg-blue-500 transition-all')}
                style={{ width: `${percent}%` }}
              />
              {item.value2 && (
                <div
                  className="bg-blue-300"
                  style={{ width: `${percent2}%` }}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================================
// Donut Chart
// ============================================================================

interface DonutChartData {
  label: string;
  value: number;
  color: string;
}

interface DonutChartProps {
  data: DonutChartData[];
  showLegend?: boolean;
  loading?: boolean;
}

export function DonutChart({ data, showLegend = true, loading }: DonutChartProps) {
  if (loading) {
    return <div className="w-full h-64 rounded-lg bg-gray-50 animate-pulse" />;
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 bg-gray-50 rounded-lg">
        <p className="text-gray-500">No data available</p>
      </div>
    );
  }

  const total = data.reduce((sum, item) => sum + item.value, 0);
  let currentAngle = 0;

  const slices = data.map((item) => {
    const sliceAngle = (item.value / total) * 360;
    const startAngle = currentAngle;
    const endAngle = currentAngle + sliceAngle;

    const start = polarToCartesian(100, 50, 45, startAngle);
    const end = polarToCartesian(100, 50, 45, endAngle);
    const largeArc = sliceAngle > 180 ? 1 : 0;

    const innerStart = polarToCartesian(100, 50, 25, startAngle);
    const innerEnd = polarToCartesian(100, 50, 25, endAngle);

    const pathData = [
      `M ${start.x} ${start.y}`,
      `A 45 45 0 ${largeArc} 1 ${end.x} ${end.y}`,
      `L ${innerEnd.x} ${innerEnd.y}`,
      `A 25 25 0 ${largeArc} 0 ${innerStart.x} ${innerStart.y}`,
      'Z',
    ].join(' ');

    currentAngle = endAngle;

    return { pathData, color: item.color, percentage: ((item.value / total) * 100).toFixed(1) };
  });

  return (
    <div className="flex flex-col items-center">
      <svg width="200" height="200" viewBox="0 0 200 200" className="mb-4">
        {slices.map((slice, i) => (
          <path key={`slice-${i}`} d={slice.pathData} fill={slice.color} />
        ))}
        {/* Center text */}
        <text x="100" y="95" textAnchor="middle" className="text-xs font-bold fill-gray-900">
          100%
        </text>
        <text x="100" y="110" textAnchor="middle" className="text-xs fill-gray-500">
          Total
        </text>
      </svg>

      {showLegend && (
        <div className="w-full grid grid-cols-2 gap-2">
          {data.map((item) => (
            <div key={`legend-${item.label}`} className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: item.color }} />
              <div>
                <p className="text-xs font-medium text-gray-700">{item.label}</p>
                <p className="text-xs text-gray-500">{((item.value / total) * 100).toFixed(1)}%</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Helper Functions
// ============================================================================

function polarToCartesian(centerX: number, centerY: number, radius: number, angleInDegrees: number) {
  const angleInRadians = (angleInDegrees - 90) * (Math.PI / 180.0);
  return {
    x: centerX + radius * Math.cos(angleInRadians),
    y: centerY + radius * Math.sin(angleInRadians),
  };
}

export function formatChartValue(value: number): string {
  if (value >= 10000000) return `₹${(value / 10000000).toFixed(1)}Cr`;
  if (value >= 100000) return `₹${(value / 100000).toFixed(1)}L`;
  if (value >= 1000) return `₹${(value / 1000).toFixed(1)}K`;
  return `₹${value.toFixed(0)}`;
}

// ============================================================================
// IMS 2.0 - Skeleton Loader Components
// ============================================================================

import clsx from 'clsx';

export function SkeletonText({ width = 'w-full', height = 'h-4' }: { width?: string; height?: string }) {
  return <div className={clsx('bg-gray-200 rounded animate-pulse', width, height)} />;
}

export function SkeletonLine({ width = 'w-full' }: { width?: string }) {
  return <div className={clsx('bg-gray-200 rounded h-3 animate-pulse', width)} />;
}

export function SkeletonCard() {
  return (
    <div className="bg-white rounded-lg p-4 border border-gray-100 shadow-sm space-y-3">
      <SkeletonText height="h-6" width="w-3/4" />
      <SkeletonText height="h-4" width="w-full" />
      <SkeletonText height="h-4" width="w-5/6" />
    </div>
  );
}

export function SkeletonTable({ rows = 3, columns = 4 }: { rows?: number; columns?: number }) {
  const getColumnWidth = (cols: number) => {
    const widths: Record<number, string> = {
      2: 'w-1/2',
      3: 'w-1/3',
      4: 'w-1/4',
      5: 'w-1/5',
      6: 'w-1/6',
    };
    return widths[cols] || 'w-1/4';
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        {Array.from({ length: columns }).map((_, i) => (
          <SkeletonText key={i} width={getColumnWidth(columns)} height="h-4" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div key={rowIdx} className="flex gap-3 p-3 bg-gray-50 rounded">
          {Array.from({ length: columns }).map((_, colIdx) => (
            <SkeletonText key={colIdx} width={getColumnWidth(columns)} height="h-3" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function SkeletonDashboard() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <SkeletonText width="w-1/3" height="h-8" />
        <SkeletonText width="w-1/2" height="h-4" />
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white rounded-lg p-4 border border-gray-100 shadow-sm">
            <SkeletonText height="h-4" width="w-2/3" />
            <SkeletonText height="h-8" width="w-1/2" className="mt-2" />
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="bg-white rounded-lg p-4 border border-gray-100 shadow-sm space-y-3">
            <SkeletonText width="w-1/3" height="h-5" />
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, j) => (
                <SkeletonLine key={j} width={`w-${100 - j * 20}%`} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default SkeletonCard;

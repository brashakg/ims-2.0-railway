// ============================================================================
// IMS 2.0 - Skeleton Loader Component (Enhanced)
// ============================================================================
// Improved loading states with better perceived performance

import clsx from 'clsx';

export type SkeletonType = 'text' | 'title' | 'image' | 'card' | 'button' | 'table-row' | 'list-item';

export interface SkeletonProps {
  type?: SkeletonType;
  width?: string | number;
  height?: string | number;
  count?: number;
  circle?: boolean;
  className?: string;
  animated?: boolean;
}

/**
 * Single skeleton loader element
 */
export function Skeleton({
  type = 'text',
  width,
  height,
  circle = false,
  className,
  animated = true,
}: SkeletonProps) {
  const baseClasses = clsx(
    'bg-gray-200 dark:bg-gray-700',
    animated && 'animate-pulse',
    circle && 'rounded-full',
    !circle && 'rounded'
  );

  const sizeClasses = {
    text: 'h-4 w-3/4',
    title: 'h-8 w-1/2',
    image: 'h-48 w-full',
    card: 'h-32 w-full',
    button: 'h-10 w-20',
    'table-row': 'h-12 w-full',
    'list-item': 'h-16 w-full',
  };

  const size = sizeClasses[type];
  const customStyle = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
  };

  return (
    <div
      className={clsx(baseClasses, size, className)}
      style={customStyle}
      role="status"
      aria-label="Loading..."
    />
  );
}

// Legacy aliases for backward compatibility
export function SkeletonText({ width = 'w-full', height = 'h-4' }: { width?: string; height?: string }) {
  return <div className={clsx('bg-gray-200 dark:bg-gray-700 rounded animate-pulse', width, height)} />;
}

export function SkeletonLine({ width = 'w-full' }: { width?: string }) {
  return <div className={clsx('bg-gray-200 dark:bg-gray-700 rounded h-3 animate-pulse', width)} />;
}

/**
 * Card skeleton with header, body, and footer
 */
export function SkeletonCard() {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg p-4 border border-gray-100 dark:border-gray-800 shadow-sm space-y-3">
      <SkeletonText height="h-6" width="w-3/4" />
      <SkeletonText height="h-4" width="w-full" />
      <SkeletonText height="h-4" width="w-5/6" />
    </div>
  );
}

/**
 * Table skeleton with rows and columns
 */
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
        <div key={rowIdx} className="flex gap-3 p-3 bg-gray-50 dark:bg-gray-800 rounded">
          {Array.from({ length: columns }).map((_, colIdx) => (
            <SkeletonText key={colIdx} width={getColumnWidth(columns)} height="h-3" />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * Grid skeleton with multiple cards
 */
export function SkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

/**
 * Dashboard skeleton with KPI cards and charts
 */
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
          <div key={i} className="bg-white dark:bg-gray-900 rounded-lg p-4 border border-gray-100 dark:border-gray-800 shadow-sm">
            <SkeletonText height="h-4" width="w-2/3" />
            <div className="mt-2">
              <SkeletonText height="h-8" width="w-1/2" />
            </div>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="bg-white dark:bg-gray-900 rounded-lg p-4 border border-gray-100 dark:border-gray-800 shadow-sm space-y-3">
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

/**
 * List skeleton with items
 */
export function SkeletonList({ items = 5 }: { items?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: items }).map((_, i) => (
        <div key={i} className="bg-white dark:bg-gray-900 rounded-lg border border-gray-100 dark:border-gray-800 p-4 flex gap-4">
          <Skeleton type="image" width={48} height={48} circle />
          <div className="flex-1 space-y-2">
            <Skeleton type="title" width="40%" />
            <Skeleton type="text" width="60%" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default SkeletonCard;

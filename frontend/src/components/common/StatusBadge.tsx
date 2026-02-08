// ============================================================================
// IMS 2.0 - Status Badge Component
// ============================================================================
// Consolidate 15+ status badge implementations across components

import clsx from 'clsx';
import React from 'react';

/**
 * Predefined status types with automatic color mapping
 * Covers: customer statuses, payment statuses, stock statuses, etc.
 */
export type StatusType =
  | 'vip'
  | 'active'
  | 'inactive'
  | 'at-risk'
  | 'pending'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'success'
  | 'in-stock'
  | 'out-of-stock'
  | 'low-stock'
  | 'damaged'
  | 'draft'
  | 'approved'
  | 'rejected'
  | 'processing'
  | 'shipped'
  | 'delivered'
  | 'returned'
  | 'refunded'
  | 'archived';

export type BadgeSize = 'xs' | 'sm' | 'md' | 'lg';
export type BadgeVariant = 'solid' | 'outline' | 'subtle';

export interface StatusBadgeProps {
  /** Status value - if it's a predefined StatusType, colors apply automatically */
  status: StatusType | string;
  /** Override automatic color with custom color classes */
  customColor?: string;
  /** Badge size (default: 'sm') */
  size?: BadgeSize;
  /** Visual variant (default: 'subtle') */
  variant?: BadgeVariant;
  /** Optional icon to display before text */
  icon?: React.ComponentType<{ className?: string }>;
  /** Optional icon to display after text */
  trailingIcon?: React.ComponentType<{ className?: string }>;
  /** Custom className for additional styling */
  className?: string;
  /** Whether to show as rounded-full (pill shape) vs rounded */
  rounded?: boolean;
  /** Capitalize status text */
  capitalize?: boolean;
  /** Optional click handler */
  onClick?: () => void;
  /** Make badge interactive/clickable */
  interactive?: boolean;
  /** Accessibility label */
  ariaLabel?: string;
}

/**
 * Get color classes based on status type
 */
const getStatusColor = (
  status: string,
  variant: BadgeVariant = 'subtle'
): string => {
  const statusLower = status.toLowerCase();

  // Solid variants - strong background with white text
  if (variant === 'solid') {
    switch (statusLower) {
      case 'vip':
        return 'bg-purple-600 text-white dark:bg-purple-700 dark:text-white';
      case 'active':
      case 'success':
        return 'bg-green-600 text-white dark:bg-green-700 dark:text-white';
      case 'completed':
      case 'delivered':
      case 'approved':
        return 'bg-blue-600 text-white dark:bg-blue-700 dark:text-white';
      case 'pending':
      case 'processing':
        return 'bg-amber-600 text-white dark:bg-amber-700 dark:text-white';
      case 'cancelled':
      case 'rejected':
      case 'failed':
      case 'at-risk':
        return 'bg-red-600 text-white dark:bg-red-700 dark:text-white';
      case 'in-stock':
        return 'bg-emerald-600 text-white dark:bg-emerald-700 dark:text-white';
      case 'out-of-stock':
      case 'damaged':
        return 'bg-red-600 text-white dark:bg-red-700 dark:text-white';
      case 'low-stock':
        return 'bg-orange-600 text-white dark:bg-orange-700 dark:text-white';
      case 'draft':
        return 'bg-gray-600 text-white dark:bg-gray-700 dark:text-white';
      case 'shipped':
        return 'bg-cyan-600 text-white dark:bg-cyan-700 dark:text-white';
      case 'returned':
        return 'bg-indigo-600 text-white dark:bg-indigo-700 dark:text-white';
      case 'refunded':
        return 'bg-teal-600 text-white dark:bg-teal-700 dark:text-white';
      case 'inactive':
        return 'bg-gray-600 text-white dark:bg-gray-700 dark:text-white';
      case 'archived':
        return 'bg-slate-600 text-white dark:bg-slate-700 dark:text-white';
      default:
        return 'bg-gray-600 text-white dark:bg-gray-700 dark:text-white';
    }
  }

  // Outline variants - border with colored text
  if (variant === 'outline') {
    switch (statusLower) {
      case 'vip':
        return 'border border-purple-300 text-purple-700 dark:border-purple-700 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20';
      case 'active':
      case 'success':
        return 'border border-green-300 text-green-700 dark:border-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20';
      case 'completed':
      case 'delivered':
      case 'approved':
        return 'border border-blue-300 text-blue-700 dark:border-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20';
      case 'pending':
      case 'processing':
        return 'border border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20';
      case 'cancelled':
      case 'rejected':
      case 'failed':
      case 'at-risk':
        return 'border border-red-300 text-red-700 dark:border-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20';
      case 'in-stock':
        return 'border border-emerald-300 text-emerald-700 dark:border-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20';
      case 'out-of-stock':
      case 'damaged':
        return 'border border-red-300 text-red-700 dark:border-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20';
      case 'low-stock':
        return 'border border-orange-300 text-orange-700 dark:border-orange-700 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20';
      case 'draft':
        return 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/20';
      case 'shipped':
        return 'border border-cyan-300 text-cyan-700 dark:border-cyan-700 dark:text-cyan-400 bg-cyan-50 dark:bg-cyan-900/20';
      case 'returned':
        return 'border border-indigo-300 text-indigo-700 dark:border-indigo-700 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-900/20';
      case 'refunded':
        return 'border border-teal-300 text-teal-700 dark:border-teal-700 dark:text-teal-400 bg-teal-50 dark:bg-teal-900/20';
      case 'inactive':
        return 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/20';
      case 'archived':
        return 'border border-slate-300 text-slate-700 dark:border-slate-700 dark:text-slate-400 bg-slate-50 dark:bg-slate-900/20';
      default:
        return 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/20';
    }
  }

  // Subtle variants (default) - light background with colored text
  switch (statusLower) {
    case 'vip':
      return 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300';
    case 'active':
    case 'success':
      return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300';
    case 'completed':
    case 'delivered':
    case 'approved':
      return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300';
    case 'pending':
    case 'processing':
      return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300';
    case 'cancelled':
    case 'rejected':
    case 'failed':
    case 'at-risk':
      return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300';
    case 'in-stock':
      return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300';
    case 'out-of-stock':
    case 'damaged':
      return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300';
    case 'low-stock':
      return 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300';
    case 'draft':
      return 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-300';
    case 'shipped':
      return 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300';
    case 'returned':
      return 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300';
    case 'refunded':
      return 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300';
    case 'inactive':
      return 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-300';
    case 'archived':
      return 'bg-slate-100 text-slate-700 dark:bg-slate-900/30 dark:text-slate-300';
    default:
      return 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-300';
  }
};

/**
 * Get size classes for badge
 */
const getSizeClasses = (size: BadgeSize): string => {
  switch (size) {
    case 'xs':
      return 'px-1.5 py-0.5 text-xs';
    case 'sm':
      return 'px-2 py-1 text-xs';
    case 'md':
      return 'px-3 py-1.5 text-sm';
    case 'lg':
      return 'px-4 py-2 text-base';
    default:
      return 'px-2 py-1 text-xs';
  }
};

export function StatusBadge({
  status,
  customColor,
  size = 'sm',
  variant = 'subtle',
  icon: Icon,
  trailingIcon: TrailingIcon,
  className,
  rounded = false,
  capitalize = true,
  onClick,
  interactive = false,
  ariaLabel,
}: StatusBadgeProps) {
  const colorClasses = customColor || getStatusColor(status, variant);
  const sizeClasses = getSizeClasses(size);
  const displayText = capitalize ? status.charAt(0).toUpperCase() + status.slice(1).replace(/-/g, ' ') : status;

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 font-medium transition-colors',
        rounded ? 'rounded-full' : 'rounded',
        sizeClasses,
        colorClasses,
        interactive && 'cursor-pointer hover:opacity-80',
        className
      )}
      onClick={onClick}
      role={interactive ? 'button' : 'status'}
      aria-label={ariaLabel || `Status: ${status}`}
      tabIndex={interactive ? 0 : -1}
    >
      {Icon && <Icon className="w-3.5 h-3.5 flex-shrink-0" />}
      <span className="truncate">{displayText}</span>
      {TrailingIcon && <TrailingIcon className="w-3.5 h-3.5 flex-shrink-0" />}
    </span>
  );
}

/**
 * Badge Group - Display multiple status badges together
 * Useful for components showing multiple statuses
 */
export interface StatusBadgeGroupProps {
  statuses: (StatusType | string)[];
  size?: BadgeSize;
  variant?: BadgeVariant;
  className?: string;
  gap?: 'xs' | 'sm' | 'md' | 'lg';
}

export function StatusBadgeGroup({
  statuses,
  size = 'sm',
  variant = 'subtle',
  className,
  gap = 'sm',
}: StatusBadgeGroupProps) {
  const gapClasses = {
    xs: 'gap-1',
    sm: 'gap-2',
    md: 'gap-3',
    lg: 'gap-4',
  };

  return (
    <div className={clsx('flex flex-wrap', gapClasses[gap], className)}>
      {statuses.map((status, idx) => (
        <StatusBadge key={`${status}-${idx}`} status={status} size={size} variant={variant} />
      ))}
    </div>
  );
}

/**
 * Numeric Badge - For displaying counts/metrics with status colors
 * Replaces inline badge patterns like scan count badges, order count badges
 */
export interface NumericBadgeProps extends Omit<StatusBadgeProps, 'status'> {
  value: number;
  label?: string;
  /** Determine status based on value thresholds */
  getStatus?: (value: number) => StatusType | string;
  /** Format the value display (e.g., for large numbers) */
  formatValue?: (value: number) => string;
}

export function NumericBadge({
  value,
  label,
  getStatus,
  formatValue,
  customColor,
  size = 'sm',
  variant = 'subtle',
  className,
  rounded = false,
  ariaLabel,
  icon: Icon,
  trailingIcon: TrailingIcon,
}: NumericBadgeProps) {
  // Determine status based on value
  let status = 'default';
  if (getStatus) {
    status = getStatus(value);
  } else {
    // Default behavior: green if > 0, gray if 0
    status = value > 0 ? 'active' : 'inactive';
  }

  const displayValue = formatValue ? formatValue(value) : value.toString();

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 font-semibold transition-colors',
        rounded ? 'rounded-full' : 'rounded',
        getSizeClasses(size),
        customColor || getStatusColor(status, variant),
        className
      )}
      aria-label={ariaLabel || `${label || 'Count'}: ${value}`}
    >
      {Icon && <Icon className="w-3.5 h-3.5 flex-shrink-0" />}
      <span>{displayValue}</span>
      {label && <span className="text-opacity-75">{label}</span>}
      {TrailingIcon && <TrailingIcon className="w-3.5 h-3.5 flex-shrink-0" />}
    </span>
  );
}

// ============================================================================
// IMS 2.0 - Photochromic Wayfarer Loader
// ============================================================================
// Custom loading animation with photochromic sunglasses effect

import { useEffect, useState, useId } from 'react';
import clsx from 'clsx';

interface PhotochromicLoaderProps {
  /** Progress percentage (0-100). If undefined, shows indeterminate animation */
  progress?: number;
  /** Size variant */
  size?: 'sm' | 'md' | 'lg';
  /** Custom message to display */
  message?: string;
  /** Whether to show full screen overlay */
  fullScreen?: boolean;
  /** Custom class name */
  className?: string;
}

// Size configurations
const sizeConfig = {
  sm: { width: 80, height: 32, fontSize: 'text-xs' },
  md: { width: 120, height: 48, fontSize: 'text-sm' },
  lg: { width: 180, height: 72, fontSize: 'text-base' },
};

export function PhotochromicLoader({
  progress,
  size = 'md',
  message,
  fullScreen = false,
  className,
}: PhotochromicLoaderProps) {
  const [animatedProgress, setAnimatedProgress] = useState(0);
  const [indeterminatePhase, setIndeterminatePhase] = useState(0);
  const isIndeterminate = progress === undefined;
  const uniqueId = useId();

  const config = sizeConfig[size];

  // Unique gradient IDs to prevent conflicts when multiple loaders are rendered
  const frameGradientId = `frameGradient-${uniqueId}`;
  const lensGradientId = `lensGradient-${uniqueId}`;
  const lensReflectionId = `lensReflection-${uniqueId}`;

  // Animate progress changes smoothly
  useEffect(() => {
    if (progress !== undefined) {
      const timer = setInterval(() => {
        setAnimatedProgress(prev => {
          const diff = progress - prev;
          if (Math.abs(diff) < 1) return progress;
          return prev + diff * 0.1;
        });
      }, 50);
      return () => clearInterval(timer);
    }
  }, [progress]);

  // Indeterminate animation cycle
  useEffect(() => {
    if (isIndeterminate) {
      const timer = setInterval(() => {
        setIndeterminatePhase(prev => (prev + 1) % 100);
      }, 30);
      return () => clearInterval(timer);
    }
  }, [isIndeterminate]);

  // Calculate lens darkness based on progress or animation phase
  const lensOpacity = isIndeterminate
    ? 0.15 + (Math.sin(indeterminatePhase * 0.0628) + 1) * 0.35 // 0.15 to 0.85
    : 0.1 + (animatedProgress / 100) * 0.8;

  const displayProgress = isIndeterminate
    ? Math.round((Math.sin(indeterminatePhase * 0.0628) + 1) * 50)
    : Math.round(animatedProgress);

  const loaderContent = (
    <div className={clsx('flex flex-col items-center gap-3', className)}>
      {/* Wayfarer Sunglasses SVG */}
      <svg
        width={config.width}
        height={config.height}
        viewBox="0 0 180 72"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="filter drop-shadow-md"
      >
        {/* Frame - outer shape */}
        <defs>
          {/* Gradient for frame */}
          <linearGradient id={frameGradientId} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#1f1f1f" />
            <stop offset="50%" stopColor="#333333" />
            <stop offset="100%" stopColor="#1f1f1f" />
          </linearGradient>
          {/* Photochromic lens gradient - animates from clear to dark */}
          <linearGradient id={lensGradientId} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#8B4513" stopOpacity={lensOpacity * 0.8} />
            <stop offset="50%" stopColor="#5D3A1A" stopOpacity={lensOpacity} />
            <stop offset="100%" stopColor="#3D2613" stopOpacity={lensOpacity * 0.9} />
          </linearGradient>
          {/* Lens reflection */}
          <linearGradient id={lensReflectionId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="white" stopOpacity="0.3" />
            <stop offset="50%" stopColor="white" stopOpacity="0.1" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Bridge (center piece connecting lenses) */}
        <path
          d="M 82 24 Q 90 28 98 24"
          stroke={`url(#${frameGradientId})`}
          strokeWidth="5"
          fill="none"
          strokeLinecap="round"
        />

        {/* Left Temple Arm (hint) */}
        <path
          d="M 8 22 L 2 20"
          stroke={`url(#${frameGradientId})`}
          strokeWidth="4"
          strokeLinecap="round"
        />

        {/* Right Temple Arm (hint) */}
        <path
          d="M 172 22 L 178 20"
          stroke={`url(#${frameGradientId})`}
          strokeWidth="4"
          strokeLinecap="round"
        />

        {/* Left Lens Frame - Wayfarer shape */}
        <path
          d="M 8 16
             L 75 12
             Q 82 12 82 20
             L 82 42
             Q 82 56 68 58
             L 24 60
             Q 8 58 8 44
             Z"
          fill={`url(#${frameGradientId})`}
          stroke="#1a1a1a"
          strokeWidth="1"
        />

        {/* Left Lens - Photochromic */}
        <path
          d="M 14 20
             L 72 16
             Q 76 16 76 22
             L 76 40
             Q 76 50 65 52
             L 26 54
             Q 14 52 14 42
             Z"
          fill={`url(#${lensGradientId})`}
          className="transition-all duration-300"
        />
        {/* Left Lens Reflection */}
        <path
          d="M 18 24 L 40 22 Q 45 22 45 26 L 45 32 Q 45 36 40 36 L 18 38 Q 16 38 16 34 Z"
          fill={`url(#${lensReflectionId})`}
        />

        {/* Right Lens Frame - Wayfarer shape */}
        <path
          d="M 98 20
             Q 98 12 105 12
             L 172 16
             L 172 44
             Q 172 58 156 60
             L 112 58
             Q 98 56 98 42
             Z"
          fill={`url(#${frameGradientId})`}
          stroke="#1a1a1a"
          strokeWidth="1"
        />

        {/* Right Lens - Photochromic */}
        <path
          d="M 104 22
             Q 104 16 108 16
             L 166 20
             L 166 42
             Q 166 52 154 54
             L 115 52
             Q 104 50 104 40
             Z"
          fill={`url(#${lensGradientId})`}
          className="transition-all duration-300"
        />
        {/* Right Lens Reflection */}
        <path
          d="M 110 24 L 135 22 Q 140 22 140 26 L 140 32 Q 140 36 135 36 L 110 38 Q 108 38 108 34 Z"
          fill={`url(#${lensReflectionId})`}
        />
      </svg>

      {/* Progress Percentage */}
      <div className="flex flex-col items-center gap-1">
        <span className={clsx(
          'font-bold text-gray-700 tabular-nums',
          config.fontSize
        )}>
          {displayProgress}%
        </span>
        {message && (
          <span className={clsx('text-gray-500', config.fontSize)}>
            {message}
          </span>
        )}
      </div>

      {/* Optional: Progress bar */}
      {!isIndeterminate && (
        <div className="w-full max-w-32 h-1.5 bg-gray-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-bv-gold-400 to-bv-gold-600 rounded-full transition-all duration-300"
            style={{ width: `${animatedProgress}%` }}
          />
        </div>
      )}
    </div>
  );

  if (fullScreen) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-white/90 backdrop-blur-sm">
        {loaderContent}
      </div>
    );
  }

  return loaderContent;
}

// ============================================================================
// Page Loader - Full screen loader for page transitions
// ============================================================================

interface PageLoaderProps {
  message?: string;
}

export function PageLoader({ message = 'Loading...' }: PageLoaderProps) {
  return <PhotochromicLoader fullScreen size="lg" message={message} />;
}

// ============================================================================
// Data Loader - Inline loader for data fetching
// ============================================================================

interface DataLoaderProps {
  progress?: number;
  message?: string;
  className?: string;
}

export function DataLoader({ progress, message, className }: DataLoaderProps) {
  return (
    <div className={clsx('flex justify-center py-8', className)}>
      <PhotochromicLoader progress={progress} size="md" message={message} />
    </div>
  );
}

// ============================================================================
// Button Loader - Small inline loader for buttons
// ============================================================================

export function ButtonLoader() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="31.4 31.4"
        className="opacity-25"
      />
      <path
        d="M 12 2 A 10 10 0 0 1 22 12"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default PhotochromicLoader;

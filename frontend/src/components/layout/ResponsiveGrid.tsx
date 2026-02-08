// ============================================================================
// IMS 2.0 - Responsive Layout Components
// ============================================================================
// Grid and flex components for mobile-first responsive design

import clsx from 'clsx';

interface ResponsiveGridProps {
  children: React.ReactNode;
  cols?: {
    sm?: number;
    md?: number;
    lg?: number;
    xl?: number;
  };
  gap?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}

/**
 * Responsive grid component - mobile-first grid layout
 * Default: 1 column on mobile, 2 on sm, 3 on md, 4 on lg
 */
export function ResponsiveGrid({
  children,
  cols = { sm: 2, md: 3, lg: 4 },
  gap = 'md',
  className,
}: ResponsiveGridProps) {
  const gapClasses = {
    sm: 'gap-2',
    md: 'gap-4',
    lg: 'gap-6',
    xl: 'gap-8',
  };

  const colsClasses = clsx(
    'grid',
    'grid-cols-1',
    cols.sm && `sm:grid-cols-${cols.sm}`,
    cols.md && `md:grid-cols-${cols.md}`,
    cols.lg && `lg:grid-cols-${cols.lg}`,
    cols.xl && `xl:grid-cols-${cols.xl}`,
    gapClasses[gap],
    className
  );

  return <div className={colsClasses}>{children}</div>;
}

interface ResponsiveContainerProps {
  children: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full';
  center?: boolean;
  padding?: 'sm' | 'md' | 'lg';
  className?: string;
}

/**
 * Responsive container - max-width container with padding
 */
export function ResponsiveContainer({
  children,
  size = 'lg',
  center = true,
  padding = 'md',
  className,
}: ResponsiveContainerProps) {
  const sizeClasses = {
    sm: 'max-w-sm',
    md: 'max-w-md',
    lg: 'max-w-4xl',
    xl: 'max-w-6xl',
    full: 'w-full',
  };

  const paddingClasses = {
    sm: 'px-2 sm:px-4',
    md: 'px-4 sm:px-6',
    lg: 'px-4 sm:px-6 lg:px-8',
  };

  return (
    <div
      className={clsx(
        sizeClasses[size],
        paddingClasses[padding],
        center && 'mx-auto',
        className
      )}
    >
      {children}
    </div>
  );
}

interface ResponsiveStackProps {
  children: React.ReactNode;
  direction?: 'col' | 'row';
  reverseOnMobile?: boolean;
  gap?: 'sm' | 'md' | 'lg' | 'xl';
  align?: 'start' | 'center' | 'end' | 'stretch';
  justify?: 'start' | 'center' | 'end' | 'between' | 'around';
  className?: string;
}

/**
 * Responsive stack - flexible stack layout
 * Automatically switches between row and column on mobile
 */
export function ResponsiveStack({
  children,
  direction = 'col',
  reverseOnMobile = false,
  gap = 'md',
  align = 'stretch',
  justify = 'start',
  className,
}: ResponsiveStackProps) {
  const gapClasses = {
    sm: 'gap-2',
    md: 'gap-4',
    lg: 'gap-6',
    xl: 'gap-8',
  };

  const alignClasses = {
    start: 'items-start',
    center: 'items-center',
    end: 'items-end',
    stretch: 'items-stretch',
  };

  const justifyClasses = {
    start: 'justify-start',
    center: 'justify-center',
    end: 'justify-end',
    between: 'justify-between',
    around: 'justify-around',
  };

  const directionClasses = direction === 'row'
    ? clsx(
      'flex-col',
      'sm:flex-row',
      reverseOnMobile && 'sm:flex-row-reverse'
    )
    : clsx(
      'flex-col',
      reverseOnMobile && 'flex-col-reverse'
    );

  return (
    <div
      className={clsx(
        'flex',
        directionClasses,
        gapClasses[gap],
        alignClasses[align],
        justifyClasses[justify],
        className
      )}
    >
      {children}
    </div>
  );
}

interface ResponsiveHiddenProps {
  children: React.ReactNode;
  hideOn?: ('mobile' | 'tablet' | 'desktop')[];
  className?: string;
}

/**
 * Responsive hidden - hide/show elements based on screen size
 */
export function ResponsiveHidden({
  children,
  hideOn = [],
  className,
}: ResponsiveHiddenProps) {
  const hiddenClasses = clsx(
    hideOn.includes('mobile') && 'hidden sm:block',
    hideOn.includes('tablet') && 'hidden md:block',
    hideOn.includes('desktop') && 'hidden md:hidden',
    className
  );

  return <div className={hiddenClasses}>{children}</div>;
}

interface ResponsiveImageProps {
  src: string;
  alt: string;
  sizes?: {
    sm?: string;
    md?: string;
    lg?: string;
  };
  className?: string;
}

/**
 * Responsive image - optimized image loading
 */
export function ResponsiveImage({
  src,
  alt,
  sizes = {
    sm: '100vw',
    md: '50vw',
    lg: '33vw',
  },
  className,
}: ResponsiveImageProps) {
  const sizesAttribute = [
    `(max-width: 640px) ${sizes.sm || '100vw'}`,
    `(max-width: 1024px) ${sizes.md || '50vw'}`,
    sizes.lg || '33vw',
  ].join(', ');

  return (
    <img
      src={src}
      alt={alt}
      sizes={sizesAttribute}
      className={clsx('w-full h-auto object-cover', className)}
      loading="lazy"
    />
  );
}

/**
 * Breakpoint helper component for development
 */
export function BreakpointIndicator() {
  return (
    <div className="fixed bottom-4 right-4 z-50 text-xs font-bold text-white bg-gray-900 rounded px-2 py-1 opacity-75">
      <span className="sm:hidden">Mobile</span>
      <span className="hidden sm:inline md:hidden">SM</span>
      <span className="hidden md:inline lg:hidden">MD</span>
      <span className="hidden lg:inline xl:hidden">LG</span>
      <span className="hidden xl:inline 2xl:hidden">XL</span>
      <span className="hidden 2xl:inline">2XL</span>
    </div>
  );
}

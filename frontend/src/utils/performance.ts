// ============================================================================
// IMS 2.0 - Performance Optimization Utilities
// ============================================================================
// Debouncing, memoization, and optimization helpers

/**
 * Debounce function - prevents excessive function calls
 * Useful for: search, autocomplete, form validation, window resize
 *
 * @example
 * const handleSearch = debounce((query) => api.search(query), 300);
 * <input onChange={(e) => handleSearch(e.target.value)} />
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout> | null = null;

  return function executedFunction(...args: Parameters<T>) {
    const later = () => {
      timeout = null;
      func(...args);
    };

    if (timeout !== null) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(later, wait);
  };
}

/**
 * Throttle function - executes function at most once per interval
 * Useful for: scroll events, resize events, frequent button clicks
 *
 * @example
 * const handleScroll = throttle(() => loadMore(), 1000);
 * window.addEventListener('scroll', handleScroll);
 */
export function throttle<T extends (...args: any[]) => any>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle: boolean = false;

  return function (...args: Parameters<T>) {
    if (!inThrottle) {
      func(...args);
      inThrottle = true;
      setTimeout(() => {
        inThrottle = false;
      }, limit);
    }
  };
}

/**
 * Memoize function results to avoid expensive recalculations
 * Useful for: filtering, sorting, complex calculations
 *
 * @example
 * const expensiveSort = memoize((items, field) => {
 *   return items.sort((a, b) => a[field] - b[field]);
 * });
 */
export function memoize<T extends (...args: any[]) => any>(
  func: T,
  options: { maxSize?: number } = {}
): T {
  const { maxSize = 100 } = options;
  const cache = new Map<string, any>();

  return ((...args: Parameters<T>) => {
    const key = JSON.stringify(args);

    if (cache.has(key)) {
      return cache.get(key);
    }

    const result = func(...args);
    cache.set(key, result);

    // Limit cache size to prevent memory leaks
    if (cache.size > maxSize) {
      const firstKeyResult = cache.keys().next();
      if (!firstKeyResult.done && firstKeyResult.value !== undefined) {
        cache.delete(firstKeyResult.value as string);
      }
    }

    return result;
  }) as T;
}

/**
 * RequestIdleCallback wrapper with fallback for older browsers
 * Defers non-urgent work until browser is idle
 *
 * @example
 * requestIdleCallback(() => {
 *   // Heavy processing work
 *   analyzeData();
 * });
 */
export function requestIdleCallback(
  callback: () => void,
  options?: { timeout?: number }
): number {
  if (typeof window !== 'undefined') {
    if ('requestIdleCallback' in window) {
      return (window as any).requestIdleCallback(callback, options);
    }

    // Fallback: use setTimeout
    const timeout = options?.timeout || 1000;
    return (window as any).setTimeout(callback, timeout);
  }

  // If window is not available, just call the callback
  callback();
  return 0;
}

/**
 * Image optimization utilities
 */
export const imageOptimization = {
  /**
   * Get optimized image URL with format and size optimization
   * Useful for: lazy loading, responsive images, format conversion
   *
   * @example
   * const optimized = imageOptimization.getOptimizedUrl(url, {
   *   format: 'webp',
   *   width: 300,
   *   quality: 80
   * });
   */
  getOptimizedUrl(
    url: string,
    options: {
      format?: 'webp' | 'jpeg' | 'png';
      width?: number;
      height?: number;
      quality?: number;
    } = {}
  ): string {
    if (!url) return '';

    // For CDN URLs, add transformation parameters
    // This is a generic example - adjust based on your image service
    const { format, width, height, quality } = options;

    // If already optimized or local, return as-is
    if (!url.includes('http') || url.includes('localhost')) {
      return url;
    }

    // Build optimization parameters
    const params = new URLSearchParams();
    if (format) params.append('fmt', format);
    if (width) params.append('w', width.toString());
    if (height) params.append('h', height.toString());
    if (quality) params.append('q', quality.toString());

    const separator = url.includes('?') ? '&' : '?';
    return params.size > 0 ? `${url}${separator}${params}` : url;
  },

  /**
   * Generate srcSet for responsive images
   * Useful for: responsive image loading based on screen size
   *
   * @example
   * const srcSet = imageOptimization.generateSrcSet(url);
   * <img src={url} srcSet={srcSet} sizes="(max-width: 600px) 100vw, 600px" />
   */
  generateSrcSet(
    url: string,
    widths: number[] = [320, 640, 1024, 1280]
  ): string {
    if (!url) return '';

    return widths
      .map(width => {
        const optimized = this.getOptimizedUrl(url, {
          width,
          format: 'webp',
          quality: 80,
        });
        return `${optimized} ${width}w`;
      })
      .join(', ');
  },

  /**
   * Check if browser supports WebP format
   */
  supportsWebP(): boolean {
    if (typeof window === 'undefined') return false;

    const canvas = document.createElement('canvas');
    if (!canvas.getContext) return false;

    const ctx = canvas.getContext('2d');
    if (!ctx) return false;

    canvas.width = 1;
    canvas.height = 1;

    try {
      ctx.fillStyle = 'rgb(0,0,0)';
      ctx.fillRect(0, 0, 1, 1);
      return canvas.toDataURL('image/webp').indexOf('image/webp') === 5;
    } catch {
      return false;
    }
  },
};

/**
 * Lazy loading image intersection observer
 */
export function observeImages(
  selector: string = 'img[data-src]',
  options: IntersectionObserverInit = {}
): IntersectionObserver | null {
  if (typeof window === 'undefined' || !('IntersectionObserver' in window)) {
    return null;
  }

  const defaultOptions: IntersectionObserverInit = {
    root: null,
    rootMargin: '50px',
    threshold: 0.01,
    ...options,
  };

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const img = entry.target as HTMLImageElement;
        const src = img.dataset.src;

        if (src) {
          img.src = src;
          img.removeAttribute('data-src');
          observer.unobserve(img);
        }
      }
    });
  }, defaultOptions);

  // Start observing all matching images
  document.querySelectorAll(selector).forEach(img => {
    observer.observe(img);
  });

  return observer;
}

/**
 * Virtual scrolling helper for large lists
 * Renders only visible items to improve performance
 *
 * @example
 * const visibleItems = getVisibleItems(items, {
 *   itemHeight: 50,
 *   containerHeight: 500,
 *   scrollOffset: scrollTop
 * });
 */
export function getVisibleItems<T>(
  items: T[],
  options: {
    itemHeight: number;
    containerHeight: number;
    scrollOffset: number;
    bufferSize?: number;
  }
): { items: T[]; startIndex: number; offset: number } {
  const { itemHeight, containerHeight, scrollOffset, bufferSize = 5 } = options;

  const startIndex = Math.max(0, Math.floor(scrollOffset / itemHeight) - bufferSize);
  const endIndex = Math.min(
    items.length,
    Math.ceil((scrollOffset + containerHeight) / itemHeight) + bufferSize
  );

  return {
    items: items.slice(startIndex, endIndex),
    startIndex,
    offset: startIndex * itemHeight,
  };
}

/**
 * Performance metrics - measure component/function performance
 */
export const performanceMetrics = {
  /**
   * Measure function execution time
   *
   * @example
   * const { time, result } = performanceMetrics.measureAsync(async () => {
   *   return await api.fetchData();
   * });
   * console.log(`Operation took ${time}ms`);
   */
  async measureAsync<T>(fn: () => Promise<T>): Promise<{ result: T; time: number }> {
    const start = performance.now();
    const result = await fn();
    const time = performance.now() - start;
    return { result, time };
  },

  measureSync<T>(fn: () => T): { result: T; time: number } {
    const start = performance.now();
    const result = fn();
    const time = performance.now() - start;
    return { result, time };
  },

  /**
   * Mark and measure named operations
   */
  mark(name: string): void {
    if (typeof window !== 'undefined' && 'performance' in window) {
      window.performance.mark(name);
    }
  },

  measure(name: string, startMark: string, endMark?: string): number {
    if (typeof window !== 'undefined' && 'performance' in window) {
      try {
        window.performance.measure(name, startMark, endMark);
        const measure = window.performance.getEntriesByName(name)[0];
        return measure?.duration || 0;
      } catch {
        return 0;
      }
    }
    return 0;
  },
};

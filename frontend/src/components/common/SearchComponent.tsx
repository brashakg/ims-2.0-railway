// ============================================================================
// IMS 2.0 - Generic Search Component
// ============================================================================
// Reusable search component to eliminate duplication in ProductSearch, CustomerSearch, etc.

import React, { useState, useCallback } from 'react';
import { Search as SearchIcon, X } from 'lucide-react';
import clsx from 'clsx';

export interface SearchItem {
  id: string;
  label: string;
  description?: string;
  [key: string]: any;
}

export interface FilterOption {
  id: string;
  label: string;
  value: string;
  count?: number;
}

export interface SearchComponentProps<T extends SearchItem> {
  // Search configuration
  placeholder?: string;
  onSearch: (query: string) => Promise<T[]>;

  // Display configuration
  renderItem: (item: T) => React.ReactNode;
  emptyMessage?: string;
  emptyIcon?: React.ComponentType<{ className?: string }>;
  loadingMessage?: string;

  // Filter configuration
  hasFilters?: boolean;
  filterOptions?: FilterOption[];
  onFilterChange?: (filter: string) => void;
  selectedFilter?: string;

  // Size configuration
  maxResults?: number;
  size?: 'sm' | 'md' | 'lg';

  // Callbacks
  onSelect?: (item: T) => void;
  onResultsChange?: (count: number) => void;

  // Optional styling
  className?: string;
  inputClassName?: string;
  resultsClassName?: string;
}

const SIZES = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
};

/**
 * Generic Search Component
 *
 * Consolidates search patterns from ProductSearch, CustomerSearch, and ProductSearchModal
 * Eliminates ~200-300 LOC of duplicated search, filter, and loading state logic
 *
 * Usage:
 * <SearchComponent<Product>
 *   placeholder="Search products..."
 *   onSearch={searchProducts}
 *   renderItem={(product) => (
 *     <div>
 *       <h3>{product.name}</h3>
 *       <p>{product.description}</p>
 *     </div>
 *   )}
 *   onSelect={handleSelectProduct}
 *   hasFilters={true}
 *   filterOptions={[
 *     { id: 'frames', label: 'Frames', value: 'frames' },
 *     { id: 'lenses', label: 'Lenses', value: 'lenses' },
 *   ]}
 *   onFilterChange={handleFilterChange}
 * />
 */
export function SearchComponent<T extends SearchItem>({
  placeholder = 'Search...',
  onSearch,
  renderItem,
  emptyMessage = 'No results found',
  emptyIcon: EmptyIcon,
  loadingMessage = 'Searching...',
  hasFilters = false,
  filterOptions = [],
  onFilterChange,
  selectedFilter,
  maxResults = 20,
  size = 'md',
  onSelect,
  onResultsChange,
  className,
  inputClassName,
  resultsClassName,
}: SearchComponentProps<T>) {
  const [searchQuery, setSearchQuery] = useState('');
  const [results, setResults] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  const handleSearch = useCallback(
    async (query: string) => {
      if (!query.trim()) {
        setResults([]);
        setHasSearched(false);
        onResultsChange?.(0);
        return;
      }

      setIsLoading(true);
      setError(null);
      setHasSearched(true);

      try {
        const data = await onSearch(query);
        const filtered = data.slice(0, maxResults);
        setResults(filtered);
        onResultsChange?.(filtered.length);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : 'Search failed, please try again'
        );
        setResults([]);
      } finally {
        setIsLoading(false);
      }
    },
    [onSearch, maxResults, onResultsChange]
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setSearchQuery(value);
    // Debounce search in production - for now search on each keystroke
    handleSearch(value);
  };

  const handleClear = () => {
    setSearchQuery('');
    setResults([]);
    setHasSearched(false);
    setError(null);
    onResultsChange?.(0);
  };

  const displayResults = results.length > 0 && !isLoading;
  const showEmpty =
    hasSearched && results.length === 0 && !isLoading && !error;
  const showError = error && hasSearched;

  return (
    <div
      className={clsx(
        'flex flex-col gap-4',
        SIZES[size],
        className
      )}
    >
      {/* Search Input */}
      <div className="relative">
        <SearchIcon className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
        <input
          type="text"
          placeholder={placeholder}
          value={searchQuery}
          onChange={handleInputChange}
          className={clsx(
            'w-full pl-10 pr-10 py-2 border border-gray-300 dark:border-gray-700',
            'rounded-lg bg-white dark:bg-gray-800',
            'text-gray-900 dark:text-white',
            'focus:outline-none focus:ring-2 focus:ring-blue-500',
            inputClassName
          )}
        />
        {searchQuery && (
          <button
            onClick={handleClear}
            className="absolute right-3 top-3 p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded"
            title="Clear search"
          >
            <X className="w-4 h-4 text-gray-400" />
          </button>
        )}
      </div>

      {/* Filters */}
      {hasFilters && filterOptions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => {
              onFilterChange?.('');
            }}
            className={clsx(
              'px-3 py-1 rounded-full text-sm font-medium transition-colors',
              !selectedFilter
                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300 hover:bg-gray-200'
            )}
          >
            All
          </button>
          {filterOptions.map(filter => (
            <button
              key={filter.id}
              onClick={() => onFilterChange?.(filter.value)}
              className={clsx(
                'px-3 py-1 rounded-full text-sm font-medium transition-colors flex items-center gap-1',
                selectedFilter === filter.value
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                  : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300 hover:bg-gray-200'
              )}
            >
              {filter.label}
              {filter.count !== undefined && (
                <span className="text-xs opacity-70">({filter.count})</span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Results */}
      <div
        className={clsx(
          'flex flex-col gap-2 max-h-96 overflow-y-auto',
          resultsClassName
        )}
      >
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600" />
            <span className="ml-2 text-gray-600 dark:text-gray-400 text-sm">
              {loadingMessage}
            </span>
          </div>
        )}

        {showError && (
          <div className="text-center py-8">
            <p className="text-red-600 dark:text-red-400 text-sm">{error}</p>
            <button
              onClick={() => handleSearch(searchQuery)}
              className="mt-2 text-blue-600 dark:text-blue-400 text-sm hover:underline"
            >
              Try again
            </button>
          </div>
        )}

        {showEmpty && (
          <div className="text-center py-8">
            {EmptyIcon && <EmptyIcon className="w-12 h-12 mx-auto mb-3 opacity-50" />}
            <p className="text-gray-500 dark:text-gray-400 text-sm">
              {emptyMessage}
            </p>
          </div>
        )}

        {displayResults &&
          results.map(item => (
            <div
              key={item.id}
              onClick={() => onSelect?.(item)}
              className={clsx(
                'p-3 border border-gray-200 dark:border-gray-700 rounded-lg',
                'hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors',
                onSelect && 'cursor-pointer'
              )}
            >
              {renderItem(item)}
            </div>
          ))}

        {displayResults && results.length >= maxResults && (
          <div className="text-center text-xs text-gray-500 dark:text-gray-400 py-2">
            Showing {results.length} of possibly more results
          </div>
        )}
      </div>
    </div>
  );
}

export default SearchComponent;

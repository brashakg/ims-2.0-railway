// ============================================================================
// IMS 2.0 - Advanced Search Component
// ============================================================================
// Global search with filters across all modules

import { useState, useCallback } from 'react';
import { Search, X, Filter } from 'lucide-react';
import clsx from 'clsx';

export interface SearchFilter {
  id: string;
  label: string;
  type: 'text' | 'select' | 'date' | 'range' | 'checkbox';
  options?: { value: string; label: string }[];
  placeholder?: string;
}

export interface SearchResult {
  id: string;
  title: string;
  description?: string;
  category: string;
  score: number;
  metadata?: Record<string, any>;
}

interface AdvancedSearchProps {
  onSearch: (query: string, filters: Record<string, any>) => Promise<SearchResult[]>;
  filters: SearchFilter[];
  placeholder?: string;
  minChars?: number;
  debounceMs?: number;
  onResultSelect?: (result: SearchResult) => void;
}

export function AdvancedSearch({
  onSearch,
  filters,
  placeholder = 'Search across all modules...',
  minChars = 2,
  debounceMs = 300,
  onResultSelect,
}: AdvancedSearchProps) {
  const [query, setQuery] = useState('');
  const [activeFilters, setActiveFilters] = useState<Record<string, any>>({});
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);
  const [debounceTimer, setDebounceTimer] = useState<ReturnType<typeof setTimeout> | null>(null);

  // Debounced search
  const handleSearch = useCallback(
    async (searchQuery: string, searchFilters: Record<string, any>) => {
      if (debounceTimer) clearTimeout(debounceTimer);

      if (searchQuery.length < minChars && Object.keys(searchFilters).length === 0) {
        setResults([]);
        setShowResults(false);
        return;
      }

      const timer = setTimeout(async () => {
        setIsLoading(true);
        try {
          const searchResults = await onSearch(searchQuery, searchFilters);
          setResults(searchResults);
          setShowResults(true);
        } catch (error) {
          console.error('Search error:', error);
          setResults([]);
        } finally {
          setIsLoading(false);
        }
      }, debounceMs);

      setDebounceTimer(timer);
    },
    [onSearch, minChars, debounceMs, debounceTimer]
  );

  const handleQueryChange = (newQuery: string) => {
    setQuery(newQuery);
    handleSearch(newQuery, activeFilters);
  };

  const handleFilterChange = (filterId: string, value: any) => {
    const newFilters = { ...activeFilters, [filterId]: value };
    setActiveFilters(newFilters);
    handleSearch(query, newFilters);
  };

  const handleClearFilters = () => {
    setActiveFilters({});
    setQuery('');
    setResults([]);
    setShowResults(false);
  };

  const handleResultClick = (result: SearchResult) => {
    onResultSelect?.(result);
    setShowResults(false);
    setQuery('');
  };

  const activeFilterCount = Object.keys(activeFilters).filter(
    (key) => activeFilters[key] !== undefined && activeFilters[key] !== ''
  ).length;

  return (
    <div className="relative w-full">
      <div className="space-y-3">
        {/* Main Search Bar */}
        <div className="relative">
          <div className="relative flex items-center">
            <Search className="absolute left-3 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              onFocus={() => results.length > 0 && setShowResults(true)}
              placeholder={placeholder}
              className="w-full pl-10 pr-12 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-bv-red-500"
              aria-label="Search"
              aria-describedby="search-filters"
            />
            <div className="absolute right-2 flex gap-1">
              {query && (
                <button
                  onClick={() => handleQueryChange('')}
                  className="p-1 hover:bg-gray-100 rounded"
                  aria-label="Clear search"
                >
                  <X className="w-4 h-4 text-gray-500" />
                </button>
              )}
              <button
                onClick={() => setShowAdvancedFilters(!showAdvancedFilters)}
                className={clsx(
                  'p-1 rounded transition-colors',
                  showAdvancedFilters || activeFilterCount > 0
                    ? 'bg-bv-red-100 text-bv-red-600'
                    : 'hover:bg-gray-100 text-gray-500'
                )}
                aria-label="Toggle advanced filters"
              >
                <Filter className="w-4 h-4" />
                {activeFilterCount > 0 && (
                  <span className="absolute -top-2 -right-2 bg-bv-red-600 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
                    {activeFilterCount}
                  </span>
                )}
              </button>
            </div>
          </div>

          {/* Advanced Filters */}
          {showAdvancedFilters && (
            <div className="absolute top-full left-0 right-0 mt-2 bg-white border border-gray-200 rounded-lg shadow-lg p-4 z-50">
              <div className="space-y-4">
                {filters.map((filter) => (
                  <div key={filter.id}>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      {filter.label}
                    </label>
                    {filter.type === 'text' && (
                      <input
                        type="text"
                        placeholder={filter.placeholder}
                        value={activeFilters[filter.id] || ''}
                        onChange={(e) => handleFilterChange(filter.id, e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-bv-red-500"
                      />
                    )}
                    {filter.type === 'select' && (
                      <select
                        value={activeFilters[filter.id] || ''}
                        onChange={(e) => handleFilterChange(filter.id, e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-bv-red-500"
                      >
                        <option value="">All {filter.label}</option>
                        {filter.options?.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    )}
                    {filter.type === 'date' && (
                      <input
                        type="date"
                        value={activeFilters[filter.id] || ''}
                        onChange={(e) => handleFilterChange(filter.id, e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-bv-red-500"
                      />
                    )}
                    {filter.type === 'checkbox' && (
                      <label className="flex items-center">
                        <input
                          type="checkbox"
                          checked={activeFilters[filter.id] || false}
                          onChange={(e) => handleFilterChange(filter.id, e.target.checked)}
                          className="w-4 h-4 text-bv-red-600 rounded"
                        />
                        <span className="ml-2 text-sm text-gray-700">{filter.label}</span>
                      </label>
                    )}
                  </div>
                ))}
              </div>
              {activeFilterCount > 0 && (
                <button
                  onClick={handleClearFilters}
                  className="w-full mt-4 px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50"
                >
                  Clear All Filters
                </button>
              )}
            </div>
          )}

          {/* Search Results Dropdown */}
          {showResults && (
            <div className="absolute top-full left-0 right-0 mt-2 bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-96 overflow-y-auto">
              {isLoading ? (
                <div className="p-4 text-center">
                  <div className="inline-block animate-spin">
                    <div className="w-5 h-5 border-2 border-bv-red-600 border-t-transparent rounded-full" />
                  </div>
                  <p className="text-sm text-gray-500 mt-2">Searching...</p>
                </div>
              ) : results.length > 0 ? (
                <div className="divide-y">
                  {results.map((result) => (
                    <button
                      key={result.id}
                      onClick={() => handleResultClick(result)}
                      className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-bv-red-500"
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <p className="font-medium text-gray-900">{result.title}</p>
                          {result.description && (
                            <p className="text-sm text-gray-500 mt-1">{result.description}</p>
                          )}
                          <p className="text-xs text-gray-400 mt-1">{result.category}</p>
                        </div>
                        {result.score < 100 && (
                          <span className="text-xs text-gray-400 ml-2">
                            {Math.round(result.score)}%
                          </span>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="p-4 text-center text-gray-500">
                  No results found
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

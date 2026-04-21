// ============================================================================
// IMS 2.0 — AutoSearch Dropdown (Store-Scoped)
// ============================================================================
// Reusable type-ahead search with dropdown results.
// ALL searches are scoped to the user's active store — never global.
//
// Usage:
//   <AutoSearch<Product>
//     fetchResults={(q, storeId) => productApi.search({ query: q, store_id: storeId })}
//     renderItem={(item) => <div>{item.name} — ₹{item.mrp}</div>}
//     onSelect={(item) => addToCart(item)}
//     placeholder="Search products..."
//     getKey={(item) => item.id}
//   />
// ============================================================================

import { useState, useRef, useEffect, useCallback } from 'react';
import { Search, X, Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

interface AutoSearchProps<T> {
  /** Fetch results — receives (query, storeId). Return array of items. */
  fetchResults: (query: string, storeId: string) => Promise<T[]>;
  /** Render each dropdown item */
  renderItem: (item: T, isHighlighted: boolean) => React.ReactNode;
  /** Called when user selects an item */
  onSelect: (item: T) => void;
  /** Unique key for each item */
  getKey: (item: T) => string;
  /** Input placeholder */
  placeholder?: string;
  /** Minimum characters before search triggers */
  minChars?: number;
  /** Debounce delay in ms */
  debounceMs?: number;
  /** Auto-focus on mount */
  autoFocus?: boolean;
  /** Additional class for the wrapper */
  className?: string;
  /** Show a "No results" message */
  emptyMessage?: string;
  /** Max results to show */
  maxResults?: number;
  /** Controlled value (for external reset) */
  value?: string;
  /** Called when input changes */
  onInputChange?: (val: string) => void;
  /** Icon override */
  icon?: React.ReactNode;
  /** Whether to clear input on select */
  clearOnSelect?: boolean;
}

export function AutoSearch<T>({
  fetchResults,
  renderItem,
  onSelect,
  getKey,
  placeholder = 'Search...',
  minChars = 2,
  debounceMs = 300,
  autoFocus = false,
  className = '',
  emptyMessage = 'No results found',
  maxResults = 8,
  value: externalValue,
  onInputChange,
  icon,
  clearOnSelect = false,
}: AutoSearchProps<T>) {
  const { user } = useAuth();
  const storeId = user?.activeStoreId || user?.storeIds?.[0] || '';

  const [query, setQuery] = useState(externalValue || '');
  const [results, setResults] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Sync external value
  useEffect(() => {
    if (externalValue !== undefined) setQuery(externalValue);
  }, [externalValue]);

  // Click outside to close
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
          inputRef.current && !inputRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < minChars) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    // Cancel previous request
    if (abortRef.current) abortRef.current.abort();
    abortRef.current = new AbortController();

    setIsLoading(true);
    try {
      const data = await fetchResults(q, storeId);
      setResults((data || []).slice(0, maxResults));
      setIsOpen(true);
      setHighlightIndex(-1);
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        setResults([]);
      }
    } finally {
      setIsLoading(false);
    }
  }, [fetchResults, storeId, minChars, maxResults]);

  const handleChange = (val: string) => {
    setQuery(val);
    onInputChange?.(val);

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(val), debounceMs);
  };

  const handleSelect = (item: T) => {
    onSelect(item);
    setIsOpen(false);
    setHighlightIndex(-1);
    if (clearOnSelect) {
      setQuery('');
      setResults([]);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || results.length === 0) {
      if (e.key === 'Enter' && query.length >= minChars) {
        e.preventDefault();
        doSearch(query);
      }
      return;
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setHighlightIndex(prev => (prev + 1) % results.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setHighlightIndex(prev => (prev - 1 + results.length) % results.length);
        break;
      case 'Enter':
        e.preventDefault();
        if (highlightIndex >= 0 && highlightIndex < results.length) {
          handleSelect(results[highlightIndex]);
        }
        break;
      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        break;
    }
  };

  const handleClear = () => {
    setQuery('');
    setResults([]);
    setIsOpen(false);
    onInputChange?.('');
    inputRef.current?.focus();
  };

  return (
    <div className={`relative ${className}`}>
      <div className="relative">
        <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : (icon || <Search className="w-4 h-4" />)}
        </div>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => { if (results.length > 0 && query.length >= minChars) setIsOpen(true); }}
          placeholder={placeholder}
          autoFocus={autoFocus}
          className="w-full pl-10 pr-8 py-2.5 bg-white border border-gray-300 rounded-lg text-sm text-gray-900 placeholder:text-gray-500 focus:ring-2 focus:ring-bv-gold-500 focus:border-bv-red-600 transition-colors"
        />
        {query && (
          <button onClick={handleClear} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Dropdown */}
      {isOpen && (
        <div ref={dropdownRef}
          className="absolute z-50 w-full mt-1 bg-white border border-gray-300 rounded-xl shadow-lg overflow-hidden max-h-80 overflow-y-auto">
          {results.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-gray-500">{emptyMessage}</div>
          ) : (
            results.map((item, idx) => (
              <button
                key={getKey(item)}
                onClick={() => handleSelect(item)}
                onMouseEnter={() => setHighlightIndex(idx)}
                className={`w-full text-left px-3 py-2.5 transition-colors border-b border-gray-200 last:border-0 ${
                  idx === highlightIndex ? 'bg-bv-red-50' : 'hover:bg-gray-100'
                }`}
              >
                {renderItem(item, idx === highlightIndex)}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

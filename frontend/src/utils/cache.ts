// ============================================================================
// IMS 2.0 - Caching Layer Utilities
// ============================================================================
// Frontend caching strategies with IndexedDB, localStorage, and in-memory cache

import { useCallback, useRef, useEffect } from 'react';

/**
 * Cache configuration options
 */
export interface CacheOptions {
  maxSize?: number; // Max items in cache
  ttl?: number; // Time to live in milliseconds
  persistent?: boolean; // Use IndexedDB for persistence
}

/**
 * Cache entry with metadata
 */
interface CacheEntry<T> {
  value: T;
  timestamp: number;
  ttl?: number;
}

/**
 * In-Memory Cache with TTL support
 */
export class MemoryCache<T> {
  private cache = new Map<string, CacheEntry<T>>();
  private maxSize: number;
  private defaultTtl?: number;

  constructor(options: CacheOptions = {}) {
    this.maxSize = options.maxSize || 100;
    this.defaultTtl = options.ttl;
  }

  set(key: string, value: T, ttl = this.defaultTtl): void {
    // Evict oldest item if cache is full
    if (this.cache.size >= this.maxSize && !this.cache.has(key)) {
      const firstKey = this.cache.keys().next().value;
      if (firstKey) this.cache.delete(firstKey);
    }

    this.cache.set(key, {
      value,
      timestamp: Date.now(),
      ttl,
    });
  }

  get(key: string): T | null {
    const entry = this.cache.get(key);

    if (!entry) {
      return null;
    }

    // Check if expired
    if (entry.ttl && Date.now() - entry.timestamp > entry.ttl) {
      this.cache.delete(key);
      return null;
    }

    return entry.value;
  }

  has(key: string): boolean {
    return this.get(key) !== null;
  }

  delete(key: string): boolean {
    return this.cache.delete(key);
  }

  clear(): void {
    this.cache.clear();
  }

  size(): number {
    return this.cache.size;
  }
}

/**
 * IndexedDB Cache for persistent storage
 */
export class IndexedDBCache {
  private dbName = 'ims-cache';
  private storeName = 'cache-store';
  private db: IDBDatabase | null = null;

  async init(): Promise<void> {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, 1);

      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        this.db = request.result;
        resolve();
      };

      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(this.storeName)) {
          db.createObjectStore(this.storeName);
        }
      };
    });
  }

  async set<T>(key: string, value: T): Promise<void> {
    if (!this.db) await this.init();

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([this.storeName], 'readwrite');
      const store = transaction.objectStore(this.storeName);
      const request = store.put({ value, timestamp: Date.now() }, key);

      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  async get<T>(key: string): Promise<T | null> {
    if (!this.db) await this.init();

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([this.storeName], 'readonly');
      const store = transaction.objectStore(this.storeName);
      const request = store.get(key);

      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        resolve(request.result?.value || null);
      };
    });
  }

  async delete(key: string): Promise<void> {
    if (!this.db) await this.init();

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([this.storeName], 'readwrite');
      const store = transaction.objectStore(this.storeName);
      const request = store.delete(key);

      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  async clear(): Promise<void> {
    if (!this.db) await this.init();

    return new Promise((resolve, reject) => {
      const transaction = this.db!.transaction([this.storeName], 'readwrite');
      const store = transaction.objectStore(this.storeName);
      const request = store.clear();

      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }
}

/**
 * useCache Hook - Combine memory and persistent caching
 *
 * @example
 * const { get, set, clear } = useCache<Product>({
 *   maxSize: 50,
 *   ttl: 5 * 60 * 1000, // 5 minutes
 *   persistent: true
 * });
 *
 * const product = await get('product:123');
 * await set('product:123', productData);
 */
export function useCache<T>(options: CacheOptions = {}) {
  const memoryCache = useRef(new MemoryCache<T>(options));
  const indexedDbCache = useRef(options.persistent ? new IndexedDBCache() : null);

  useEffect(() => {
    if (indexedDbCache.current) {
      indexedDbCache.current.init().catch(console.error);
    }
  }, []);

  const get = useCallback(
    async (key: string): Promise<T | null> => {
      // Try memory cache first
      const memValue = memoryCache.current.get(key);
      if (memValue !== null) {
        return memValue;
      }

      // Try IndexedDB if persistent
      if (indexedDbCache.current) {
        try {
          const dbValue = await indexedDbCache.current.get<T>(key);
          if (dbValue) {
            // Restore to memory cache
            memoryCache.current.set(key, dbValue, options.ttl);
            return dbValue;
          }
        } catch (error) {
          console.error('IndexedDB get error:', error);
        }
      }

      return null;
    },
    [options.ttl]
  );

  const set = useCallback(
    async (key: string, value: T): Promise<void> => {
      memoryCache.current.set(key, value, options.ttl);

      if (indexedDbCache.current) {
        try {
          await indexedDbCache.current.set(key, value);
        } catch (error) {
          console.error('IndexedDB set error:', error);
        }
      }
    },
    [options.ttl]
  );

  const clear = useCallback(async () => {
    memoryCache.current.clear();

    if (indexedDbCache.current) {
      try {
        await indexedDbCache.current.clear();
      } catch (error) {
        console.error('IndexedDB clear error:', error);
      }
    }
  }, []);

  return {
    get,
    set,
    clear,
    has: (key: string) => memoryCache.current.has(key),
    size: () => memoryCache.current.size(),
  };
}

/**
 * Browser Storage Cache - Simple key-value store
 * Useful for non-sensitive user preferences, filters, etc.
 */
export const storageCache = {
  set<T>(key: string, value: T): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      console.warn('Storage cache set error:', error);
    }
  },

  get<T>(key: string, defaultValue?: T): T | null {
    try {
      const item = localStorage.getItem(key);
      return item ? JSON.parse(item) : defaultValue || null;
    } catch (error) {
      console.warn('Storage cache get error:', error);
      return defaultValue || null;
    }
  },

  remove(key: string): void {
    try {
      localStorage.removeItem(key);
    } catch (error) {
      console.warn('Storage cache remove error:', error);
    }
  },

  clear(): void {
    try {
      localStorage.clear();
    } catch (error) {
      console.warn('Storage cache clear error:', error);
    }
  },
};

/**
 * Request deduplication - Prevent duplicate API calls
 */
export class RequestDeduplicator {
  private pendingRequests = new Map<string, Promise<any>>();

  async deduplicate<T>(
    key: string,
    requestFn: () => Promise<T>
  ): Promise<T> {
    // Return existing promise if request is in flight
    if (this.pendingRequests.has(key)) {
      return this.pendingRequests.get(key)!;
    }

    // Create and store promise
    const promise = requestFn()
      .then(result => {
        this.pendingRequests.delete(key);
        return result;
      })
      .catch(error => {
        this.pendingRequests.delete(key);
        throw error;
      });

    this.pendingRequests.set(key, promise);
    return promise;
  }

  clear(): void {
    this.pendingRequests.clear();
  }
}

/**
 * Global request deduplicator instance
 */
export const globalDeduplicator = new RequestDeduplicator();

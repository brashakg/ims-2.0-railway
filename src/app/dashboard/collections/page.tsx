'use client';

import { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import {
  RefreshCw,
  Loader2,
  Search,
  FolderOpen,
  Image as ImageIcon,
  ExternalLink,
  AlertCircle,
  CheckCircle,
  Tag,
  Filter,
  Eye,
  EyeOff,
} from 'lucide-react';
import Topbar from '@/components/Topbar';

type Segment = 'all' | 'active' | 'hidden';

const SEGMENTS: Array<{ key: Segment; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'hidden', label: 'Hidden' },
];

interface Collection {
  id: string;
  shopifyCollectionId: string;
  title: string;
  handle: string | null;
  description: string | null;
  collectionType: string;
  sortOrder: string | null;
  imageUrl: string | null;
  seoTitle: string | null;
  published: boolean;
  productsCount: number;
  locallyModified: boolean;
  lastSyncedAt: string | null;
  createdAt: string;
  updatedAt: string;
  _count: { products: number };
}

export default function CollectionsPage() {
  const { data: session } = useSession();
  const isAdmin = (session?.user as any)?.role === 'ADMIN';

  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [segment, setSegment] = useState<Segment>('all');

  // Filter locally since API doesn't have a "published" filter yet — cheap
  // since the page loads at most 100 collections.
  const visibleCollections = collections.filter((c) => {
    if (segment === 'active') return c.published;
    if (segment === 'hidden') return !c.published;
    return true;
  });
  const counts = {
    all: collections.length,
    active: collections.filter((c) => c.published).length,
    hidden: collections.filter((c) => !c.published).length,
  };

  const fetchCollections = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (typeFilter) params.set('type', typeFilter);
      params.set('limit', '100');

      const res = await fetch(`/api/collections?${params.toString()}`);
      const json = await res.json();
      if (json.success) {
        setCollections(json.data);
      }
    } catch (error) {
      console.error('Error fetching collections:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCollections();
  }, [typeFilter]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      fetchCollections();
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage('');
    try {
      const res = await fetch('/api/collections/sync', { method: 'POST' });
      const json = await res.json();
      if (json.success) {
        setSyncMessage(json.message);
        await fetchCollections();
      } else {
        setSyncMessage(`Error: ${json.error}`);
      }
    } catch (error) {
      setSyncMessage('Failed to sync collections');
    } finally {
      setSyncing(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <>
      <Topbar
        title="Collections"
        subtitle="Synced from Shopify, editable locally"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Collections' }]}
        primaryAction={null}
        actions={
          isAdmin ? (
            <button
              type="button"
              onClick={handleSync}
              disabled={syncing}
              className="polaris-btn"
            >
              {syncing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5" />
              )}
              Sync from Shopify
            </button>
          ) : null
        }
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
        {/* Header replaced by Topbar above. Sync button moved into Topbar
            actions; the previous flex header block is gone. */}

        {/* Sync status message */}
        {syncMessage && (
          <div
            className={`mb-4 p-3 rounded-lg text-sm flex items-center gap-2 ${
              syncMessage.startsWith('Error')
                ? 'bg-red-50 text-red-800 border border-red-200'
                : 'bg-green-50 text-green-800 border border-green-200'
            }`}
          >
            {syncMessage.startsWith('Error') ? (
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
            ) : (
              <CheckCircle className="w-4 h-4 flex-shrink-0" />
            )}
            {syncMessage}
          </div>
        )}

        {/* Shopify-style status segment tabs */}
        <div className="bg-white rounded-lg shadow mb-4 p-3">
          <div className="flex items-center gap-2 flex-wrap">
            {SEGMENTS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setSegment(s.key)}
                className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                  segment === s.key
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                <span>{s.label}</span>
                <span
                  className={`text-[11px] px-1.5 rounded-full ${
                    segment === s.key ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {counts[s.key]}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Search + type filter */}
        <div className="bg-white rounded-lg shadow mb-6 p-4">
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search collections..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-10 pr-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
              />
            </div>
            <div className="flex gap-2 items-center">
              <span className="text-xs text-slate-500 uppercase tracking-wider">
                Type
              </span>
              {[
                { val: '', label: 'All' },
                { val: 'CUSTOM', label: 'Custom' },
                { val: 'SMART', label: 'Smart' },
              ].map((t) => (
                <button
                  key={t.val}
                  type="button"
                  onClick={() => setTypeFilter(t.val)}
                  className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                    typeFilter === t.val
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Collections Grid */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
          </div>
        ) : visibleCollections.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-12 text-center">
            <FolderOpen className="w-12 h-12 text-gray-300 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-gray-900 mb-2">No collections found</h3>
            <p className="text-sm text-gray-500 mb-4">
              {search
                ? 'Try a different search term.'
                : segment !== 'all'
                  ? `No ${segment} collections. Switch to "All" to see everything.`
                  : 'Click "Sync from Shopify" to pull your collections.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {visibleCollections.map((col) => (
              <Link
                key={col.id}
                href={`/dashboard/collections/${col.id}`}
                className="bg-white rounded-lg shadow hover:shadow-md transition-shadow overflow-hidden group"
              >
                {/* Image */}
                <div className="h-36 bg-gray-100 relative overflow-hidden">
                  {col.imageUrl ? (
                    <img
                      src={col.imageUrl}
                      alt={col.title}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <ImageIcon className="w-10 h-10 text-gray-300" />
                    </div>
                  )}

                  {/* Top-right stack: type + published status */}
                  <div className="absolute top-2 right-2 flex flex-col gap-1 items-end">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${
                        col.collectionType === 'SMART'
                          ? 'bg-purple-50 text-purple-700 border-purple-200'
                          : 'bg-blue-50 text-blue-700 border-blue-200'
                      }`}
                    >
                      {col.collectionType}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${
                        col.published
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-slate-100 text-slate-600 border-slate-200'
                      }`}
                    >
                      {col.published ? (
                        <Eye className="w-3 h-3" />
                      ) : (
                        <EyeOff className="w-3 h-3" />
                      )}
                      {col.published ? 'Active' : 'Hidden'}
                    </span>
                  </div>

                  {/* Locally modified badge */}
                  {col.locallyModified && (
                    <span className="absolute top-2 left-2 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-50 text-amber-700 border border-amber-200">
                      Modified
                    </span>
                  )}
                </div>

                {/* Content */}
                <div className="p-4">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-semibold text-gray-900 group-hover:text-blue-600 transition-colors truncate flex-1">
                      {col.title}
                    </h3>
                    {col.shopifyCollectionId && (
                      <a
                        href={`https://${(process.env.NEXT_PUBLIC_SHOPIFY_STORE_URL || 'bokaro-better-vision.myshopify.com')}/admin/collections/${col.shopifyCollectionId.split('/').pop()}`}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-slate-400 hover:text-blue-600 flex-shrink-0"
                        title="Open in Shopify"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    )}
                  </div>
                  {col.description && (
                    <p className="text-sm text-gray-500 mt-1 line-clamp-2">{col.description}</p>
                  )}

                  <div className="flex items-center gap-3 mt-3 text-xs text-gray-400">
                    <span className="flex items-center gap-1">
                      <Tag className="w-3 h-3" />
                      {col.productsCount} products
                    </span>
                    {col.handle && (
                      <span className="truncate">/{col.handle}</span>
                    )}
                  </div>

                  <div className="mt-2 text-xs text-gray-400">
                    Last synced: {formatDate(col.lastSyncedAt)}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}

        {/* Summary */}
        {!loading && visibleCollections.length > 0 && (
          <div className="mt-4 text-sm text-gray-500 text-center">
            Showing {visibleCollections.length} of {collections.length} collection{collections.length !== 1 ? 's' : ''}
            {typeFilter && ` · type ${typeFilter.toLowerCase()}`}
            {segment !== 'all' && ` · ${segment}`}
          </div>
        )}
      </div>
    </>
  );
}

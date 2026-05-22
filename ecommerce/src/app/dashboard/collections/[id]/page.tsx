'use client';

import { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import { useRouter, useParams } from 'next/navigation';
import {
  ArrowLeft,
  Save,
  Loader2,
  ExternalLink,
  Tag,
  Image as ImageIcon,
  AlertCircle,
  CheckCircle,
  Trash2,
  Package,
} from 'lucide-react';

interface CollectionProduct {
  id: string;
  position: number;
  product: {
    id: string;
    title: string | null;
    brand: string;
    modelNo: string | null;
    category: string;
    status: string;
    mrp: number;
    sku: string | null;
    shopifyProductId: string | null;
    images: Array<{ url: string }>;
  };
}

interface CollectionDetail {
  id: string;
  shopifyCollectionId: string;
  title: string;
  handle: string | null;
  description: string | null;
  descriptionHtml: string | null;
  collectionType: string;
  sortOrder: string | null;
  imageUrl: string | null;
  imageAlt: string | null;
  seoTitle: string | null;
  seoDescription: string | null;
  published: boolean;
  productsCount: number;
  rules: string | null;
  disjunctive: boolean;
  locallyModified: boolean;
  lastSyncedAt: string | null;
  products: CollectionProduct[];
}

export default function CollectionDetailPage() {
  const router = useRouter();
  const params = useParams();
  const { data: session } = useSession();
  const isAdmin = (session?.user as any)?.role === 'ADMIN';
  const collectionId = params.id as string;

  const [collection, setCollection] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState('');

  // Editable fields
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [seoTitle, setSeoTitle] = useState('');
  const [seoDescription, setSeoDescription] = useState('');
  const [sortOrder, setSortOrder] = useState('');

  const fetchCollection = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/collections/${collectionId}`);
      const json = await res.json();
      if (json.success && json.data) {
        const c = json.data;
        setCollection(c);
        setTitle(c.title);
        setDescription(c.description || '');
        setSeoTitle(c.seoTitle || '');
        setSeoDescription(c.seoDescription || '');
        setSortOrder(c.sortOrder || 'BEST_SELLING');
      }
    } catch (error) {
      console.error('Error fetching collection:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (collectionId) fetchCollection();
  }, [collectionId]);

  const handleSave = async () => {
    if (!isAdmin) return;
    setSaving(true);
    setSaveMessage('');
    try {
      const res = await fetch(`/api/collections/${collectionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          description,
          descriptionHtml: `<p>${description}</p>`,
          seoTitle,
          seoDescription,
          sortOrder,
          pushToShopify: true,
        }),
      });
      const json = await res.json();
      if (json.success) {
        const shopifyMsg = json.shopifySync?.success
          ? 'Saved & synced to Shopify'
          : `Saved locally (Shopify: ${json.shopifySync?.message || 'not synced'})`;
        setSaveMessage(shopifyMsg);
        await fetchCollection();
      } else {
        setSaveMessage(`Error: ${json.error}`);
      }
    } catch (error) {
      setSaveMessage('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const parsedRules = collection?.rules ? JSON.parse(collection.rules) : [];

  const sortOptions = [
    { value: 'BEST_SELLING', label: 'Best Selling' },
    { value: 'ALPHA_ASC', label: 'Alphabetically (A-Z)' },
    { value: 'ALPHA_DESC', label: 'Alphabetically (Z-A)' },
    { value: 'PRICE_ASC', label: 'Price (Low to High)' },
    { value: 'PRICE_DESC', label: 'Price (High to Low)' },
    { value: 'CREATED', label: 'Date Created (Oldest)' },
    { value: 'CREATED_DESC', label: 'Date Created (Newest)' },
    { value: 'MANUAL', label: 'Manual' },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (!collection) {
    return (
      <div className="p-6 bg-gray-50 min-h-screen">
        <div className="max-w-4xl mx-auto text-center py-12">
          <AlertCircle className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-gray-900">Collection not found</h2>
          <button
            onClick={() => router.push('/dashboard/collections')}
            className="mt-4 text-blue-600 hover:underline"
          >
            Back to collections
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 bg-gray-50 min-h-screen">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => router.push('/dashboard/collections')}
            className="p-2 hover:bg-gray-200 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-gray-600" />
          </button>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-gray-900">{collection.title}</h1>
            <div className="flex items-center gap-3 mt-1">
              <span
                className={`px-2 py-0.5 rounded text-xs font-medium ${
                  collection.collectionType === 'SMART'
                    ? 'bg-purple-100 text-purple-700'
                    : 'bg-blue-100 text-blue-700'
                }`}
              >
                {collection.collectionType}
              </span>
              {collection.locallyModified && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-700">
                  Locally Modified
                </span>
              )}
              {collection.handle && (
                <span className="text-xs text-gray-400">/{collection.handle}</span>
              )}
            </div>
          </div>
          {isAdmin && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:bg-gray-400 transition-colors"
            >
              {saving ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {saving ? 'Saving...' : 'Save & Sync'}
            </button>
          )}
        </div>

        {/* Save message */}
        {saveMessage && (
          <div
            className={`mb-4 p-3 rounded-lg text-sm flex items-center gap-2 ${
              saveMessage.startsWith('Error')
                ? 'bg-red-50 text-red-800 border border-red-200'
                : 'bg-green-50 text-green-800 border border-green-200'
            }`}
          >
            {saveMessage.startsWith('Error') ? (
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
            ) : (
              <CheckCircle className="w-4 h-4 flex-shrink-0" />
            )}
            {saveMessage}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left column — Edit form */}
          <div className="lg:col-span-2 space-y-6">
            {/* Basic Info */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">Collection Details</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    disabled={!isAdmin}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Description
                  </label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    disabled={!isAdmin}
                    rows={4}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 resize-y"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Sort Order</label>
                  <select
                    value={sortOrder}
                    onChange={(e) => setSortOrder(e.target.value)}
                    disabled={!isAdmin}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
                  >
                    {sortOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* SEO */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">SEO</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">SEO Title</label>
                  <input
                    type="text"
                    value={seoTitle}
                    onChange={(e) => setSeoTitle(e.target.value)}
                    disabled={!isAdmin}
                    placeholder="Appears in search results"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
                  />
                  <p className="text-xs text-gray-400 mt-1">{seoTitle.length}/70 characters</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    SEO Description
                  </label>
                  <textarea
                    value={seoDescription}
                    onChange={(e) => setSeoDescription(e.target.value)}
                    disabled={!isAdmin}
                    rows={3}
                    placeholder="Appears in search results"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 resize-y"
                  />
                  <p className="text-xs text-gray-400 mt-1">{seoDescription.length}/160 characters</p>
                </div>
              </div>
            </div>

            {/* Smart Collection Rules */}
            {collection.collectionType === 'SMART' && parsedRules.length > 0 && (
              <div className="bg-white rounded-lg shadow p-6">
                <h2 className="text-lg font-semibold text-gray-900 mb-4">
                  Conditions
                  <span className="text-sm font-normal text-gray-500 ml-2">
                    (Products must match {collection.disjunctive ? 'any' : 'all'} conditions)
                  </span>
                </h2>
                <div className="space-y-2">
                  {parsedRules.map((rule: any, idx: number) => (
                    <div
                      key={idx}
                      className="flex items-center gap-2 bg-gray-50 rounded-lg p-3 text-sm"
                    >
                      <span className="font-medium text-gray-700 capitalize">
                        {rule.column?.replace(/_/g, ' ').toLowerCase()}
                      </span>
                      <span className="text-gray-500">{rule.relation?.toLowerCase()}</span>
                      <span className="font-medium text-blue-600">{rule.condition}</span>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-gray-400 mt-3">
                  Smart collection rules are managed in Shopify. Sync to pull latest changes.
                </p>
              </div>
            )}

            {/* Products in Collection */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">
                Products ({collection.products.length})
              </h2>
              {collection.products.length === 0 ? (
                <div className="text-center py-8">
                  <Package className="w-10 h-10 text-gray-300 mx-auto mb-3" />
                  <p className="text-sm text-gray-500">No products linked to this collection locally.</p>
                  <p className="text-xs text-gray-400 mt-1">
                    Product-collection links sync when both the product and collection exist locally.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {collection.products.map((cp) => (
                    <div
                      key={cp.id}
                      className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors"
                    >
                      {cp.product.images[0]?.url ? (
                        <img
                          src={cp.product.images[0].url}
                          alt={cp.product.title || ''}
                          className="w-10 h-10 rounded object-cover"
                        />
                      ) : (
                        <div className="w-10 h-10 rounded bg-gray-200 flex items-center justify-center">
                          <ImageIcon className="w-4 h-4 text-gray-400" />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {cp.product.title || `${cp.product.brand} ${cp.product.modelNo}`}
                        </p>
                        <p className="text-xs text-gray-500">
                          {cp.product.category} · {cp.product.sku || 'No SKU'} · ₹{cp.product.mrp}
                        </p>
                      </div>
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          cp.product.status === 'PUBLISHED'
                            ? 'bg-green-100 text-green-700'
                            : cp.product.status === 'DRAFT'
                              ? 'bg-gray-100 text-gray-700'
                              : 'bg-red-100 text-red-700'
                        }`}
                      >
                        {cp.product.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right column — Info sidebar */}
          <div className="space-y-6">
            {/* Image */}
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-sm font-semibold text-gray-900 mb-3">Collection Image</h3>
              {collection.imageUrl ? (
                <img
                  src={collection.imageUrl}
                  alt={collection.imageAlt || collection.title}
                  className="w-full rounded-lg object-cover"
                />
              ) : (
                <div className="w-full h-32 bg-gray-100 rounded-lg flex items-center justify-center">
                  <ImageIcon className="w-8 h-8 text-gray-300" />
                </div>
              )}
            </div>

            {/* Metadata */}
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-sm font-semibold text-gray-900 mb-3">Info</h3>
              <dl className="space-y-3 text-sm">
                <div>
                  <dt className="text-gray-500">Type</dt>
                  <dd className="font-medium text-gray-900">{collection.collectionType}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Products (Shopify)</dt>
                  <dd className="font-medium text-gray-900">{collection.productsCount}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Handle</dt>
                  <dd className="font-medium text-gray-900">{collection.handle || '—'}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Published</dt>
                  <dd className="font-medium text-gray-900">{collection.published ? 'Yes' : 'No'}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Last Synced</dt>
                  <dd className="font-medium text-gray-900">
                    {collection.lastSyncedAt
                      ? new Date(collection.lastSyncedAt).toLocaleDateString('en-IN', {
                          day: 'numeric',
                          month: 'short',
                          year: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                      : 'Never'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Shopify ID</dt>
                  <dd className="font-mono text-xs text-gray-600 break-all">
                    {collection.shopifyCollectionId}
                  </dd>
                </div>
              </dl>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// IMS 2.0 - Collection Browse (unification step-13)
// ============================================================================
// Read-only client for the MATERIALISED collection browse surface, served by
// /api/v1/collections (NOT the role-gated admin editor under
// /online-store/collections). Fast-path over the `collection_products` view:
//   GET /collections                    -> browsable collections (+ member count)
//   GET /collections/{handle}/products   -> paged members (rich rows)
//   POST /collections/{handle}/refresh    -> recompute membership (catalogue roles)
// Every read is FAIL-SOFT: any error resolves to an empty result so the browse
// view always renders. Import directly (not via the api barrel).

import api from './client';

const BASE = '/collections';

export interface BrowseCollection {
  id: string;
  handle: string;
  title: string;
  collection_type: 'CUSTOM' | 'SMART';
  products_count: number;
  published: boolean;
  sort_priority: number;
}

export interface BrowseProduct {
  product_id: string;
  sku: string;
  title?: string | null;
  brand?: string | null;
  category?: string | null;
  mrp?: number | null;
  offer_price?: number | null;
  image?: string | null;
}

export interface BrowsePage {
  handle: string;
  collection_id?: string;
  title?: string;
  collection_type?: 'CUSTOM' | 'SMART' | null;
  products: BrowseProduct[];
  total: number;
  skip: number;
  limit: number;
}

export const collectionBrowseApi = {
  /** List browsable collections (published by default). Fail-soft -> []. */
  async list(params?: { published?: boolean; collection_type?: 'CUSTOM' | 'SMART' }): Promise<BrowseCollection[]> {
    try {
      const { data } = await api.get(BASE, { params });
      return (data?.collections ?? []) as BrowseCollection[];
    } catch {
      return [];
    }
  },

  /** Page a collection's materialised membership. Fail-soft -> empty page. */
  async products(handle: string, skip = 0, limit = 24): Promise<BrowsePage> {
    const empty: BrowsePage = { handle, products: [], total: 0, skip, limit };
    try {
      const { data } = await api.get(`${BASE}/${encodeURIComponent(handle)}/products`, {
        params: { skip, limit },
      });
      return (data ?? empty) as BrowsePage;
    } catch {
      return empty;
    }
  },

  /** Force-recompute a collection's membership (catalogue roles). Throws on
   *  failure so the caller can toast it. */
  async refresh(handle: string): Promise<{ products_count: number }> {
    const { data } = await api.post(`${BASE}/${encodeURIComponent(handle)}/refresh`);
    return data;
  },
};

export default collectionBrowseApi;

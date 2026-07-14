// ============================================================================
// IMS 2.0 - Catalogue PDF + Temporary Collections API client
// ============================================================================
// Share product options with a customer as a branded PDF:
//   POST /catalogue/pdf              -> a PDF blob (from a collection OR product_ids)
//   POST /catalogue/temp-collections -> save a hand-picked selection as a
//                                       temporary (<=7d, auto-expiring) set
//   GET  /catalogue/temp-collections -> list live temp sets
//   DELETE /catalogue/temp-collections/{id}
//
// Import DIRECTLY from this module (never the services/api barrel — TS2614).
// The PDF call returns a BLOB; the interceptor's camelCase aliasing skips blobs.

import api from './client';

export interface CataloguePdfOptions {
  collectionId?: string;
  productIds?: string[];
  includeDetails?: boolean;
  includeMrp?: boolean;
  title?: string;
}

export interface TempCollection {
  collection_id: string;
  id: string;
  name: string;
  handle: string;
  is_temporary: true;
  products_count: number;
  expires_at: string | null;
  created_by?: string | null;
  created_at?: string | null;
}

/** Pull a filename out of a Content-Disposition header, else a sensible default. */
function filenameFromDisposition(disposition: unknown, fallback: string): string {
  if (typeof disposition === 'string') {
    const m = /filename="?([^"]+)"?/i.exec(disposition);
    if (m && m[1]) return m[1];
  }
  return fallback;
}

/** Trigger a browser download of a Blob. */
function triggerDownload(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so the click has consumed the URL.
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

export const cataloguePdfApi = {
  /** Build + download the catalogue PDF. Throws (with the server message) on
   *  failure so the caller can toast it. */
  generatePdf: async (opts: CataloguePdfOptions): Promise<void> => {
    const body: Record<string, unknown> = {
      include_details: !!opts.includeDetails,
      include_mrp: opts.includeMrp !== false, // default ON
    };
    if (opts.collectionId) body.collection_id = opts.collectionId;
    if (opts.productIds && opts.productIds.length) body.product_ids = opts.productIds;
    if (opts.title) body.title = opts.title;

    const res = await api.post('/catalogue/pdf', body, { responseType: 'blob' });
    const blob = res.data as Blob;
    const fallback = `${(opts.title || 'catalogue').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'catalogue'}.pdf`;
    const filename = filenameFromDisposition(
      res.headers?.['content-disposition'],
      fallback,
    );
    triggerDownload(blob, filename);
  },

  /** Save a hand-picked selection as a temporary collection. */
  createTempCollection: async (args: {
    name: string;
    productIds: string[];
    validityDays?: number;
  }): Promise<TempCollection> => {
    const res = await api.post('/catalogue/temp-collections', {
      name: args.name,
      product_ids: args.productIds,
      validity_days: args.validityDays ?? 7,
    });
    return (res.data?.collection ?? res.data) as TempCollection;
  },

  /** List the caller's live temporary collections. Fail-soft -> []. */
  listTempCollections: async (): Promise<TempCollection[]> => {
    try {
      const res = await api.get('/catalogue/temp-collections');
      const arr = res.data?.collections;
      return (Array.isArray(arr) ? arr : []) as TempCollection[];
    } catch {
      return [];
    }
  },

  /** Remove a temporary collection early. */
  deleteTempCollection: async (collectionId: string): Promise<void> => {
    await api.delete(`/catalogue/temp-collections/${encodeURIComponent(collectionId)}`);
  },
};

export default cataloguePdfApi;

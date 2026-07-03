// ============================================================================
// IMS 2.0 - Catalog Dictionary API (/catalog-field-options)
// ============================================================================
// Settings -> Catalog Dictionary: owner-managed allowed-value lists for
// Add-Product attribute fields, in two scopes: an "All categories" list per
// field plus per-category overrides (a category list REPLACES the global one
// for that category, so same-named fields never bleed across categories).
// Brand values are NOT here — they live in the Brand Master (/admin/brands).
// Import DIRECTLY from this module, not the api barrel (TS2614).

import api from './client';

export interface CatalogDictionaryResponse {
  // "All categories" lists: field name -> saved values
  fields: Record<string, string[]>;
  // per-category overrides: canonical category -> field name -> values
  by_category: Record<string, Record<string, string[]>>;
  // field names whose values are managed by the Brand Master instead
  brand_managed_fields: string[];
}

export const catalogDictionaryApi = {
  list: async (): Promise<CatalogDictionaryResponse> => {
    const response = await api.get('/catalog-field-options');
    const data = response.data as Partial<CatalogDictionaryResponse>;
    return {
      fields: data.fields || {},
      by_category: data.by_category || {},
      brand_managed_fields: data.brand_managed_fields || ['brand_name', 'subbrand'],
    };
  },

  // Replace the whole list for one field in one scope. `category` (canonical,
  // e.g. "SUNGLASS") scopes the list to that category only; omit it for the
  // "All categories" list. An empty list un-configures that scope.
  save: async (
    field: string,
    items: string[],
    category?: string
  ): Promise<{ field: string; category: string | null; items: string[] }> => {
    const response = await api.patch(
      `/catalog-field-options/${encodeURIComponent(field)}`,
      { items, ...(category ? { category } : {}) }
    );
    return response.data as { field: string; category: string | null; items: string[] };
  },
};

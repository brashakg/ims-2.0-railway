// ============================================================================
// IMS 2.0 - Catalog Dictionary API (/catalog-field-options)
// ============================================================================
// Settings -> Catalog Dictionary: owner-managed allowed-value lists for
// Add-Product attribute fields. Brand values are NOT here — they live in the
// Brand Master (/admin/brands). Import DIRECTLY from this module, not the
// api barrel (TS2614).

import api from './client';

export interface CatalogDictionaryResponse {
  // field name -> saved values (possibly empty = saved-but-unconfigured)
  fields: Record<string, string[]>;
  // field names whose values are managed by the Brand Master instead
  brand_managed_fields: string[];
}

export const catalogDictionaryApi = {
  list: async (): Promise<CatalogDictionaryResponse> => {
    const response = await api.get('/catalog-field-options');
    return response.data as CatalogDictionaryResponse;
  },

  // Replace the whole list for one field. An empty list un-configures the
  // field (it becomes free-form again).
  save: async (field: string, items: string[]): Promise<{ field: string; items: string[] }> => {
    const response = await api.patch(
      `/catalog-field-options/${encodeURIComponent(field)}`,
      { items }
    );
    return response.data as { field: string; items: string[] };
  },
};

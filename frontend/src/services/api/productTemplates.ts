// ============================================================================
// IMS 2.0 - Product Templates API (Phase C of the product-add redesign, #143)
// ----------------------------------------------------------------------------
// Named, reusable Quick Add field-value snapshots. A template stores the
// ProductFormValues blob so loading one prefills the Quick Add form; the user
// then edits + saves a REAL product (POST /products) which mints the SKU.
// Templates never create products themselves.
//
// IMPORTANT: import this DIRECTLY from this module, NOT via the
// services/api/index.ts barrel — the barrel re-export fails to resolve for
// newly-added services (TS2614), a repeat gotcha on returnsApi / shippingApi.
// ============================================================================

import api from './client';
import type { ProductFormValues } from '../../pages/catalog/productAddShared';

export interface ProductTemplate {
  template_id: string;
  name: string;
  category?: string | null;
  // The Quick Add form-values blob, stored verbatim + handed back on load.
  payload: ProductFormValues;
  created_by?: string | null;
  created_by_name?: string | null;
  created_at?: string | null;
}

export interface TemplateListResponse {
  templates: ProductTemplate[];
  total: number;
}

export const productTemplatesApi = {
  /** List saved templates, newest first. Optionally filter by category code. */
  list: async (category?: string): Promise<TemplateListResponse> => {
    const response = await api.get('/product-templates', {
      params: category ? { category } : undefined,
    });
    return response.data as TemplateListResponse;
  },

  /** Save the current Quick Add form values as a named template. */
  create: async (
    name: string,
    payload: ProductFormValues,
    category?: string,
  ): Promise<ProductTemplate> => {
    const response = await api.post('/product-templates', { name, payload, category });
    return response.data as ProductTemplate;
  },

  /** Delete a template (owner, or ADMIN / SUPERADMIN). */
  remove: async (templateId: string): Promise<void> => {
    await api.delete(`/product-templates/${templateId}`);
  },
};

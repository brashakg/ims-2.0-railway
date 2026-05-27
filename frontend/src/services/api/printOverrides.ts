// ============================================================================
// IMS 2.0 - Per-entity print template overrides API
// ============================================================================
// CRUD over /api/v1/print-overrides. The owner edits content fields per
// (entity_id, template_key) from the Print module's content editor; the
// production renderer (LegalHeader / StaffHeader) merges them on top of
// sensible CGST/NCAHP-compliant defaults at render time.
//
// IMPORT THIS MODULE DIRECTLY -- not via the services/api barrel
// (newly-added FE service barrel re-exports do not resolve, TS2614).

import api from './client';

export type PrintTemplateKey =
  | 'tax_invoice'
  | 'thermal_receipt'
  | 'rx_card'
  | 'job_card'
  | 'grn'
  | 'z_report';

export interface PrintOverrideFields {
  header_subtitle?: string;
  declaration_text?: string;
  signatory_name?: string;
  signatory_designation?: string;
  drug_licence_no?: string;
  ncahp_uid?: string;
  dmc_reg?: string;
  footer_terms?: string;
  logo_url?: string;
  retention_years?: number;
  reverse_charge_default?: boolean;
}

export interface PrintOverride {
  override_id: string;
  entity_id: string;
  template_key: PrintTemplateKey;
  fields: PrintOverrideFields;
  exists?: boolean;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

export interface PrintOverrideListResponse {
  entity_id: string;
  overrides: PrintOverride[];
  total: number;
}

export interface PrintTemplateMeta {
  templates: Array<{ key: PrintTemplateKey; label: string }>;
  fields: Array<{
    name: keyof PrintOverrideFields;
    label: string;
    description?: string;
    type?: string;
  }>;
}

export const printOverridesApi = {
  list: async (entityId: string): Promise<PrintOverrideListResponse> => {
    const res = await api.get('/print-overrides', {
      params: { entity_id: entityId },
    });
    return res.data;
  },

  get: async (
    entityId: string,
    templateKey: PrintTemplateKey
  ): Promise<PrintOverride> => {
    const res = await api.get(`/print-overrides/${entityId}/${templateKey}`);
    return res.data;
  },

  upsert: async (
    entityId: string,
    templateKey: PrintTemplateKey,
    fields: PrintOverrideFields
  ): Promise<PrintOverride> => {
    const res = await api.put(`/print-overrides/${entityId}/${templateKey}`, {
      fields,
    });
    return res.data;
  },

  remove: async (
    entityId: string,
    templateKey: PrintTemplateKey
  ): Promise<{ deleted: boolean; entity_id: string; template_key: string }> => {
    const res = await api.delete(`/print-overrides/${entityId}/${templateKey}`);
    return res.data;
  },

  meta: async (): Promise<PrintTemplateMeta> => {
    const res = await api.get('/print-overrides/_meta/templates');
    return res.data;
  },
};

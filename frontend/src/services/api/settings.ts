// ============================================================================
// IMS 2.0 - Settings & Integrations API
// ============================================================================

import api from './client';

// Catalog types (mirrored from backend _INTEGRATION_CATALOG shape)
export interface IntegrationFieldDef {
  key: string;
  label: string;
  secret: boolean;
  placeholder?: string;
  help?: string;
  optional?: boolean;
}

export interface IntegrationCatalogEntry {
  type: string;
  name: string;
  description: string;
  category: string;
  fields: IntegrationFieldDef[];
}

// ============================================================================
// Settings API - Extended settings management
// ============================================================================

export const settingsApi = {
  // Profile
  getProfile: async () => {
    const response = await api.get('/settings/profile');
    return response.data;
  },

  updateProfile: async (data: { full_name?: string; phone?: string; email?: string }) => {
    const response = await api.put('/settings/profile', data);
    return response.data;
  },

  changePassword: async (data: { current_password: string; new_password: string }) => {
    const response = await api.post('/settings/profile/change-password', data);
    return response.data;
  },

  getPreferences: async () => {
    const response = await api.get('/settings/profile/preferences');
    return response.data;
  },

  updatePreferences: async (preferences: Record<string, unknown>) => {
    const response = await api.put('/settings/profile/preferences', preferences);
    return response.data;
  },

  // Business Settings
  getBusinessSettings: async () => {
    const response = await api.get('/settings/business');
    return response.data;
  },

  updateBusinessSettings: async (settings: {
    company_name?: string;
    company_short_name?: string;
    tagline?: string;
    logo_url?: string;
    primary_color?: string;
    secondary_color?: string;
    support_email?: string;
    support_phone?: string;
    website?: string;
    address?: string;
  }) => {
    const response = await api.put('/settings/business', settings);
    return response.data;
  },

  /**
   * Upload a company/brand logo image. Returns a stable `logo_url`
   * (`/api/v1/settings/business/logo/{file_id}`) that should be saved
   * onto the business settings doc so it persists across reloads.
   * ADMIN / SUPERADMIN only (enforced server-side).
   */
  uploadLogo: async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const response = await api.post('/settings/business/logo', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data as {
      message: string;
      logo_url: string;
      url: string;
      file_id: string;
      filename: string;
      mime_type: string;
      size_bytes: number;
    };
  },

  /**
   * Fetch the stored logo bytes (the serve endpoint requires the JWT, so
   * an <img src> can't hit it directly) and hand back an object URL for
   * inline rendering. Caller is responsible for URL.revokeObjectURL.
   * Accepts either the full `/api/v1/settings/business/logo/{id}` path or
   * a bare file id.
   */
  getLogoObjectUrl: async (logoUrlOrId: string): Promise<string> => {
    // The shared axios instance is already based at `/api/v1`, so strip a
    // leading `/api/v1` if the caller passed the absolute stored URL.
    let path = logoUrlOrId;
    const marker = '/settings/business/logo/';
    const idx = path.indexOf(marker);
    if (idx >= 0) {
      path = path.slice(idx); // -> /settings/business/logo/{id}
    } else if (!path.startsWith('/')) {
      path = `${marker}${path}`; // bare file id
    }
    const response = await api.get<Blob>(path, { responseType: 'blob' });
    return URL.createObjectURL(response.data);
  },

  // Tax Settings
  getTaxSettings: async () => {
    const response = await api.get('/settings/tax');
    return response.data;
  },

  updateTaxSettings: async (settings: {
    gst_enabled?: boolean;
    company_gstin?: string;
    default_gst_rate?: number;
    hsn_validation?: boolean;
    e_invoice_enabled?: boolean;
    e_way_bill_enabled?: boolean;
    e_way_bill_threshold?: number;
  }) => {
    const response = await api.put('/settings/tax', settings);
    return response.data;
  },

  // Invoice Settings
  getInvoiceSettings: async () => {
    const response = await api.get('/settings/invoice');
    return response.data;
  },

  updateInvoiceSettings: async (settings: {
    invoice_prefix?: string;
    invoice_start_number?: number;
    financial_year?: string;
    show_logo_on_invoice?: boolean;
    show_terms_on_invoice?: boolean;
    default_terms?: string;
    default_warranty_days?: number;
    show_qr_code?: boolean;
  }) => {
    const response = await api.put('/settings/invoice', settings);
    return response.data;
  },

  // Notification Templates
  getNotificationTemplates: async () => {
    const response = await api.get('/settings/notifications/templates');
    return response.data;
  },

  getNotificationTemplate: async (templateId: string) => {
    const response = await api.get(`/settings/notifications/templates/${templateId}`);
    return response.data;
  },

  updateNotificationTemplate: async (templateId: string, template: {
    template_id?: string;
    template_type?: string;
    trigger_event?: string;
    is_enabled?: boolean;
    subject?: string;
    content?: string;
    variables?: string[];
  }) => {
    const response = await api.put(`/settings/notifications/templates/${templateId}`, template);
    return response.data;
  },

  createNotificationTemplate: async (template: {
    template_id: string;
    template_type: string;
    trigger_event: string;
    is_enabled: boolean;
    subject?: string;
    content: string;
    variables: string[];
  }) => {
    const response = await api.post('/settings/notifications/templates', template);
    return response.data;
  },

  deleteNotificationTemplate: async (templateId: string) => {
    const response = await api.delete(`/settings/notifications/templates/${templateId}`);
    return response.data;
  },

  testNotification: async (templateId: string, testPhone?: string, testEmail?: string) => {
    const response = await api.post('/settings/notifications/test', { template_id: templateId, test_phone: testPhone, test_email: testEmail });
    return response.data;
  },

  // Notification Providers
  getNotificationProviders: async () => {
    const response = await api.get('/settings/notifications/providers');
    return response.data;
  },

  updateNotificationProvider: async (provider: {
    provider: string;
    api_key: string;
    api_secret?: string;
    sender_id?: string;
    webhook_url?: string;
    is_active: boolean;
  }) => {
    const response = await api.put('/settings/notifications/providers', provider);
    return response.data;
  },

  // Notification Logs
  getNotificationLogs: async (params?: {
    customer_id?: string;
    template_id?: string;
    channel?: string;
    status?: string;
    start_date?: string;
    end_date?: string;
    limit?: number;
    offset?: number;
  }) => {
    const response = await api.get('/settings/notifications/logs', { params });
    return response.data;
  },

  // Send Notification
  sendNotification: async (notification: {
    template_id: string;
    customer_id?: string;
    phone?: string;
    email?: string;
    variables: Record<string, string>;
    channel?: 'SMS' | 'WHATSAPP' | 'EMAIL';
  }) => {
    // Notification send + bulk send live under /marketing on the backend
    // (see routers/marketing.py). The FE used to call /notifications/...
    // which 404'd — corrected here to use the actual mounted prefix.
    const response = await api.post('/marketing/notifications/send', notification);
    return response.data;
  },

  // Bulk Notifications
  sendBulkNotifications: async (notifications: {
    template_id: string;
    recipients: Array<{
      customer_id?: string;
      phone?: string;
      email?: string;
      variables: Record<string, string>;
    }>;
    channel?: 'SMS' | 'WHATSAPP' | 'EMAIL';
  }) => {
    const response = await api.post('/marketing/notifications/send-bulk', notifications);
    return response.data;
  },

  // Printer Settings
  getPrinterSettings: async () => {
    const response = await api.get('/settings/printers');
    return response.data;
  },

  updatePrinterSettings: async (settings: {
    receipt_printer_name?: string;
    receipt_printer_width?: number;
    label_printer_name?: string;
    label_size?: string;
    auto_print_receipt?: boolean;
    auto_print_job_card?: boolean;
    copies_per_print?: number;
    qz_enabled?: boolean;
    auto_print_stage_sticker?: boolean;
  }) => {
    const response = await api.put('/settings/printers', settings);
    return response.data;
  },

  getAvailablePrinters: async () => {
    const response = await api.get('/settings/printers/available');
    return response.data;
  },

  // Discount Rules
  getDiscountRules: async () => {
    const response = await api.get('/settings/discount-rules');
    return response.data;
  },

  updateDiscountRules: async (rules: Record<string, Record<string, number>>) => {
    const response = await api.put('/settings/discount-rules', rules);
    return response.data;
  },

  // Integrations
  getIntegrationsCatalog: async () => {
    const response = await api.get('/settings/integrations/catalog');
    return response.data as { catalog: IntegrationCatalogEntry[] };
  },

  getIntegrations: async () => {
    const response = await api.get('/settings/integrations');
    return response.data;
  },

  getIntegration: async (integrationType: string) => {
    const response = await api.get(`/settings/integrations/${integrationType}`);
    return response.data;
  },

  updateIntegration: async (integrationType: string, config: {
    integration_type: string;
    enabled: boolean;
    config: Record<string, unknown>;
  }) => {
    const response = await api.put(`/settings/integrations/${integrationType}`, config);
    return response.data;
  },

  testIntegration: async (integrationType: string) => {
    const response = await api.post(`/settings/integrations/${integrationType}/test`);
    return response.data;
  },

  // System Settings
  getSystemSettings: async () => {
    const response = await api.get('/settings/system');
    return response.data;
  },

  updateSystemSettings: async (settings: Record<string, unknown>) => {
    const response = await api.put('/settings/system', settings);
    return response.data;
  },

  // Audit Logs
  getAuditLogs: async (params?: {
    entity_type?: string;
    entity_id?: string;
    user_id?: string;
    action?: string;
    store_id?: string;
    start_date?: string; // inclusive YYYY-MM-DD
    end_date?: string; // inclusive YYYY-MM-DD
    limit?: number;
    offset?: number;
  }) => {
    const response = await api.get('/settings/audit-logs', { params });
    return response.data;
  },

  getAuditSummary: async () => {
    const response = await api.get('/settings/audit-logs/summary');
    return response.data;
  },

  // Admin Control Panel
  getAdminControls: async () => {
    const response = await api.get('/settings/admin-controls');
    return response.data;
  },

  updateAdminControls: async (controls: {
    store_modules?: Record<string, Record<string, boolean>>;
    discount_limits?: Array<{ roleId: string; maxDiscountPercent: number; requiresApproval: boolean; approvalThreshold: number }>;
    operational_rules?: Record<string, boolean | number | string>;
  }) => {
    const response = await api.put('/settings/admin-controls', controls);
    return response.data;
  },

  // Approval Workflows
  getApprovalWorkflows: async () => {
    const response = await api.get('/settings/approval-workflows');
    return response.data as { workflows: ApprovalWorkflow[] };
  },

  updateApprovalWorkflows: async (workflows: ApprovalWorkflow[]) => {
    const response = await api.put('/settings/approval-workflows', { workflows });
    return response.data;
  },

  // TDS rates (national set; editable by SUPERADMIN). Effective = code defaults
  // overlaid with any saved overrides; the AP/payment path applies them.
  getTdsRates: async (): Promise<{ rates: Record<string, number>; defaults: Record<string, number>; overrides: Record<string, number> }> => {
    const response = await api.get('/settings/tds-rates');
    return response.data;
  },
  updateTdsRates: async (rates: Record<string, number>) => {
    const response = await api.put('/settings/tds-rates', { rates });
    return response.data;
  },
};

export interface ApprovalWorkflow {
  id: string;
  type: string;
  name: string;
  description: string;
  isEnabled: boolean;
  thresholdType: 'AMOUNT' | 'PERCENTAGE' | 'ALWAYS';
  thresholdValue?: number | null;
  approverRoles: string[];
  escalationTimeout?: number | null;
  notifyOnRequest: boolean;
  notifyOnApproval: boolean;
}

// ============================================================================
// Admin API - Integrations
// ============================================================================

export const adminIntegrationApi = {
  // Razorpay
  getRazorpayConfig: async () => {
    const response = await api.get('/admin/integrations/razorpay');
    return response.data;
  },

  setRazorpayConfig: async (data: { keyId: string; keySecret: string; webhookSecret?: string; enabled: boolean }) => {
    // SEC-5: backend RazorpayConfig model uses snake_case field names.
    const payload: Record<string, unknown> = {
      key_id: data.keyId,
      key_secret: data.keySecret,
      enabled: data.enabled,
    };
    if (data.webhookSecret !== undefined) payload.webhook_secret = data.webhookSecret;
    const response = await api.post('/admin/integrations/razorpay', payload);
    return response.data;
  },

  testRazorpayConnection: async () => {
    const response = await api.post('/admin/integrations/razorpay/test');
    return response.data;
  },

  // WhatsApp
  getWhatsappConfig: async () => {
    const response = await api.get('/admin/integrations/whatsapp');
    return response.data;
  },

  setWhatsappConfig: async (data: { apiKey: string; phoneNumberId: string; businessId: string; enabled: boolean }) => {
    // SEC-5: backend WhatsappConfig uses snake_case field names.
    const response = await api.post('/admin/integrations/whatsapp', {
      api_key: data.apiKey,
      phone_number_id: data.phoneNumberId,
      business_id: data.businessId,
      enabled: data.enabled,
    });
    return response.data;
  },

  testWhatsappConnection: async () => {
    const response = await api.post('/admin/integrations/whatsapp/test');
    return response.data;
  },

  // Tally
  getTallyConfig: async () => {
    const response = await api.get('/admin/integrations/tally');
    return response.data;
  },

  setTallyConfig: async (data: { serverUrl: string; companyName: string; syncInterval: number; enabled: boolean }) => {
    // SEC-5: backend TallyConfig uses snake_case field names.
    const response = await api.post('/admin/integrations/tally', {
      server_url: data.serverUrl,
      company_name: data.companyName,
      sync_interval: data.syncInterval,
      enabled: data.enabled,
    });
    return response.data;
  },

  testTallyConnection: async () => {
    const response = await api.post('/admin/integrations/tally/test');
    return response.data;
  },

  // Tally per-store voucher exports (Phase I-6)
  // Each (date, store_id) tuple has its own row in tally_exports.
  // The CA's RDP-Tally companies (one per branch) consume these.
  listTallyExports: async (date: string) => {
    const response = await api.get('/admin/integrations/tally/exports', {
      params: { date },
    });
    return response.data as {
      date: string;
      total: number;
      exports: Array<{
        store_id: string;
        store_code: string;
        store_name: string;
        voucher_count: number;
        balanced: boolean;
        balance_check?: { ok: boolean; mismatch_count: number; batch_delta: number };
        generated_at: string;
        download_url: string;
      }>;
    };
  },

  /** Stream the raw XML for one (date, store) tuple. Triggers a browser
   * download via Content-Disposition. Filename is server-generated and
   * gets `_UNBALANCED` suffix when the row failed validation. */
  downloadTallyVoucherXml: async (date: string, storeId: string): Promise<void> => {
    const response = await api.get('/admin/integrations/tally/voucher.xml', {
      params: { date, store_id: storeId },
      responseType: 'blob',
    });
    // Pull the server-generated filename from the Content-Disposition header.
    // Falls back to a sensible default if the header is missing.
    const cd = response.headers?.['content-disposition'] || '';
    const match = cd.match(/filename="?([^"]+)"?/i);
    const filename = match?.[1] || `tally_${storeId}_${date}.xml`;
    const blob = new Blob([response.data], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },

  regenerateTallyExport: async (date: string, storeId?: string) => {
    const payload: { date: string; store_id?: string } = { date };
    if (storeId) payload.store_id = storeId;
    const response = await api.post('/admin/integrations/tally/regenerate', payload);
    return response.data as {
      ok: boolean;
      items_synced: number;
      notes: string;
      error: string | null;
      date: string;
      store_id: string | null;
    };
  },

  // Shopify
  getShopifyConfig: async () => {
    const response = await api.get('/admin/integrations/shopify');
    return response.data;
  },

  setShopifyConfig: async (data: { shopUrl: string; apiKey: string; apiSecret: string; accessToken: string; enabled: boolean }) => {
    // SEC-5: backend ShopifyConfig uses snake_case field names.
    const response = await api.post('/admin/integrations/shopify', {
      shop_url: data.shopUrl,
      api_key: data.apiKey,
      api_secret: data.apiSecret,
      access_token: data.accessToken,
      enabled: data.enabled,
    });
    return response.data;
  },

  testShopifyConnection: async () => {
    const response = await api.post('/admin/integrations/shopify/test');
    return response.data;
  },

  // SMS Gateway
  getSmsConfig: async () => {
    const response = await api.get('/admin/integrations/sms');
    return response.data;
  },

  setSmsConfig: async (data: { provider: string; apiKey: string; senderId: string; enabled: boolean }) => {
    // SEC-5: backend SmsConfig uses snake_case field names.
    const response = await api.post('/admin/integrations/sms', {
      provider: data.provider,
      api_key: data.apiKey,
      sender_id: data.senderId,
      enabled: data.enabled,
    });
    return response.data;
  },
};

// ============================================================================
// E2 - Policy Matrix API (/settings/policies/*)
// Schema-driven: the registry IS the form definition. Resolution store>entity>
// global>env>default; secrets masked in GET; per-key write-roles enforced server-side.
// ============================================================================

export interface PolicySpecPublic {
  key: string;
  type: string;            // bool|int|float|percent|money_paisa|string|json|enum|csv_int
  default: any;
  scopes: string[];        // subset of global|entity|store
  write_roles: string[];
  group: string;
  label: string;
  help?: string | null;
  secret?: boolean;
  minimum?: number | null;
  maximum?: number | null;
  enum?: string[] | null;
}

export interface PolicyEffective {
  key: string;
  value: any;              // masked ("****") when secret
  source: string;          // store|entity|global|env|default
  scope: string;           // resolved scope address
  type: string;
  secret?: boolean;
}

export const policiesApi = {
  getRegistry: async (): Promise<{ policies: PolicySpecPublic[]; groups: Record<string, PolicySpecPublic[]> }> => {
    const r = await api.get('/settings/policies/registry');
    return r.data;
  },
  getAll: async (scope?: string): Promise<{ scope: string; policies: Record<string, PolicyEffective> }> => {
    const r = await api.get('/settings/policies', { params: scope ? { scope } : {} });
    return r.data;
  },
  set: async (key: string, value: any, scope?: Record<string, string> | null): Promise<PolicyEffective> => {
    const r = await api.put(`/settings/policies/${encodeURIComponent(key)}`, { value, scope: scope || null });
    return r.data;
  },
  clear: async (key: string, scope?: string): Promise<PolicyEffective> => {
    const r = await api.delete(`/settings/policies/${encodeURIComponent(key)}`, { params: scope ? { scope } : {} });
    return r.data;
  },
};

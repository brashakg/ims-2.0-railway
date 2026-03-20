// ============================================================================
// IMS 2.0 - Settings & Integrations API
// ============================================================================

import api from './client';

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
    const response = await api.post('/notifications/send', notification);
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
    const response = await api.post('/notifications/send-bulk', notifications);
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
};

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
    const response = await api.post('/admin/integrations/razorpay', data);
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
    const response = await api.post('/admin/integrations/whatsapp', data);
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
    const response = await api.post('/admin/integrations/tally', data);
    return response.data;
  },

  testTallyConnection: async () => {
    const response = await api.post('/admin/integrations/tally/test');
    return response.data;
  },

  // Shopify
  getShopifyConfig: async () => {
    const response = await api.get('/admin/integrations/shopify');
    return response.data;
  },

  setShopifyConfig: async (data: { shopUrl: string; apiKey: string; apiSecret: string; accessToken: string; enabled: boolean }) => {
    const response = await api.post('/admin/integrations/shopify', data);
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
    const response = await api.post('/admin/integrations/sms', data);
    return response.data;
  },
};

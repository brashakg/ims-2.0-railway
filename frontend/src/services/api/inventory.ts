// ============================================================================
// IMS 2.0 - Inventory / Stock API
// ============================================================================

import api from './client';

export const inventoryApi = {
  getStock: async (storeId: string, productId?: string) => {
    const response = await api.get('/inventory/stock', { params: { store_id: storeId, product_id: productId } });
    return response.data;
  },

  getStockByBarcode: async (barcode: string) => {
    const response = await api.get(`/inventory/barcode/${barcode}`);
    return response.data;
  },

  searchByBarcode: async (barcode: string, storeId: string) => {
    // Search for product by barcode in specific store
    const response = await api.get(`/inventory/barcode/${barcode}`, { params: { store_id: storeId } });
    return response.data;
  },

  getLowStock: async (storeId: string) => {
    const response = await api.get('/inventory/low-stock', { params: { store_id: storeId } });
    return response.data;
  },

  getExpiringStock: async (storeId: string, days: number = 30) => {
    const response = await api.get('/inventory/expiring', { params: { store_id: storeId, days } });
    return response.data;
  },

  createTransfer: async (data: { fromStoreId: string; toStoreId: string; items: Array<{ stockId: string; quantity: number }> }) => {
    // Real transfer API is on /transfers, not /inventory/transfers
    const response = await api.post('/transfers', {
      transfer_type: 'inter_store',
      from_location_id: data.fromStoreId,
      from_location_name: data.fromStoreId,
      to_location_id: data.toStoreId,
      to_location_name: data.toStoreId,
      priority: 'normal',
      items: data.items.map(i => ({
        product_id: i.stockId,
        product_name: i.stockId,
        sku: '',
        quantity_requested: i.quantity,
        unit_cost: 0,
      })),
    });
    return response.data;
  },

  getTransfers: async (storeId: string, direction: 'incoming' | 'outgoing') => {
    const response = await api.get('/transfers', { params: { store_id: storeId, direction } });
    return response.data;
  },

  // Stock Aging / Non-Moving Report
  getAgingReport: async (storeId: string, params?: { category?: string; classification?: string; min_days?: number }) => {
    const response = await api.get('/inventory/aging', { params: { store_id: storeId, ...params } });
    return response.data;
  },

  // Stock Count / Physical Verification
  getStockCounts: async (storeId: string, status?: string) => {
    const response = await api.get('/inventory/stock-count', { params: { store_id: storeId, status } });
    return response.data;
  },

  startStockCount: async (data: { category?: string; zone?: string; notes?: string }) => {
    const response = await api.post('/inventory/stock-count/start', data);
    return response.data;
  },

  recordCountItem: async (countId: string, item: { product_id: string; product_name?: string; sku?: string; counted_quantity: number; notes?: string }) => {
    const response = await api.post(`/inventory/stock-count/${countId}/items`, item);
    return response.data;
  },

  completeStockCount: async (countId: string, notes?: string) => {
    const response = await api.post(`/inventory/stock-count/${countId}/complete`, notes ? { notes } : {});
    return response.data;
  },

  getStockCount: async (countId: string) => {
    const response = await api.get(`/inventory/stock-count/${countId}`);
    return response.data;
  },
};

// ============================================================================
// Vendors API
// ============================================================================

export const vendorsApi = {
  // Vendors
  getVendors: async (params?: { search?: string; is_active?: boolean }) => {
    const response = await api.get('/vendors', { params });
    return response.data;
  },

  getVendor: async (vendorId: string) => {
    const response = await api.get(`/vendors/${vendorId}`);
    return response.data;
  },

  createVendor: async (vendor: {
    legal_name: string;
    trade_name: string;
    vendor_type?: string;
    gstin_status: string;
    gstin?: string;
    address: string;
    city: string;
    state: string;
    mobile: string;
    email?: string;
    credit_days?: number;
  }) => {
    const response = await api.post('/vendors', vendor);
    return response.data;
  },

  updateVendor: async (vendorId: string, updates: Partial<{
    legal_name: string;
    trade_name: string;
    address: string;
    city: string;
    state: string;
    mobile: string;
    email: string;
    credit_days: number;
    is_active: boolean;
  }>) => {
    const response = await api.put(`/vendors/${vendorId}`, updates);
    return response.data;
  },

  // Purchase Orders
  getPurchaseOrders: async (params?: { vendor_id?: string; status?: string; store_id?: string }) => {
    const response = await api.get('/vendors/purchase-orders', { params });
    return response.data;
  },

  getPurchaseOrder: async (poId: string) => {
    const response = await api.get(`/vendors/purchase-orders/${poId}`);
    return response.data;
  },

  createPurchaseOrder: async (po: {
    vendor_id: string;
    delivery_store_id: string;
    items: Array<{
      product_id: string;
      product_name: string;
      sku: string;
      quantity: number;
      unit_price: number;
    }>;
    expected_date?: string;
    notes?: string;
  }) => {
    const response = await api.post('/vendors/purchase-orders', po);
    return response.data;
  },

  sendPurchaseOrder: async (poId: string) => {
    const response = await api.post(`/vendors/purchase-orders/${poId}/send`);
    return response.data;
  },

  cancelPurchaseOrder: async (poId: string, reason: string) => {
    const response = await api.post(`/vendors/purchase-orders/${poId}/cancel`, null, { params: { reason } });
    return response.data;
  },

  // GRN (Goods Received Notes)
  getGRNs: async (params?: { store_id?: string; status?: string; po_id?: string }) => {
    const response = await api.get('/vendors/grn', { params });
    return response.data;
  },

  getGRN: async (grnId: string) => {
    const response = await api.get(`/vendors/grn/${grnId}`);
    return response.data;
  },

  createGRN: async (grn: {
    po_id: string;
    vendor_invoice_no: string;
    vendor_invoice_date: string;
    items: Array<{
      po_item_id: string;
      product_id: string;
      received_qty: number;
      accepted_qty: number;
      rejected_qty?: number;
      rejection_reason?: string;
    }>;
    notes?: string;
  }) => {
    const response = await api.post('/vendors/grn', grn);
    return response.data;
  },

  acceptGRN: async (grnId: string) => {
    const response = await api.post(`/vendors/grn/${grnId}/accept`);
    return response.data;
  },

  escalateGRN: async (grnId: string, note: string) => {
    const response = await api.post(`/vendors/grn/${grnId}/escalate`, null, { params: { note } });
    return response.data;
  },

  // Get pending GRN items for stock acceptance (combines PO items awaiting GRN)
  getPendingStock: async (storeId: string) => {
    const response = await api.get('/vendors/grn', { params: { store_id: storeId, status: 'PENDING' } });
    return response.data;
  },
};

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

  // INV-5: backend /transfers has no "direction" param; uses store_id (either side).
  // Direction filtering is done client-side by comparing from_location_id.
  getTransfers: async (storeId: string, _direction?: string) => {
    const response = await api.get('/transfers', { params: { store_id: storeId } });
    return response.data;
  },

  // Stock Aging / Non-Moving Report
  getAgingReport: async (storeId: string, params?: { category?: string; classification?: string; min_days?: number }) => {
    const response = await api.get('/inventory/aging', { params: { store_id: storeId, ...params } });
    return response.data;
  },

  // Unified Stock Alerts (dead stock / low stock / reorder / overstock / fast-moving)
  getStockAlerts: async (
    storeId?: string,
    params?: { dead_days?: number; lead_time_days?: number; limit?: number }
  ) => {
    const response = await api.get('/inventory/alerts', {
      params: { ...(storeId ? { store_id: storeId } : {}), ...params },
    });
    return response.data;
  },

  // Serialized Inventory (serial-number tracking for high-value units)
  getSerials: async (storeId?: string, params?: { status?: string; search?: string }) => {
    const response = await api.get('/inventory/serials', {
      params: { ...(storeId ? { store_id: storeId } : {}), ...params },
    });
    return response.data;
  },

  createSerial: async (data: {
    productId: string;
    serialNumber: string;
    status: string;
    locationCode?: string;
    purchaseDate?: string;
    warrantyMonths?: number;
    warrantyExpiryDate?: string;
    supplierBatch?: string;
    notes?: string;
    soldTo?: string;
    soldDate?: string;
    storeId?: string;
  }) => {
    const response = await api.post('/inventory/serials', {
      product_id: data.productId,
      serial_number: data.serialNumber,
      status: data.status,
      location_code: data.locationCode,
      purchase_date: data.purchaseDate,
      warranty_months: data.warrantyMonths,
      warranty_expiry_date: data.warrantyExpiryDate,
      supplier_batch: data.supplierBatch,
      notes: data.notes,
      sold_to: data.soldTo,
      sold_date: data.soldDate,
      store_id: data.storeId,
    });
    return response.data;
  },

  updateSerial: async (
    serialId: string,
    data: {
      status?: string;
      locationCode?: string;
      purchaseDate?: string;
      warrantyMonths?: number;
      warrantyExpiryDate?: string;
      supplierBatch?: string;
      notes?: string;
      soldTo?: string;
      soldDate?: string;
    }
  ) => {
    const response = await api.patch(`/inventory/serials/${serialId}`, {
      status: data.status,
      location_code: data.locationCode,
      purchase_date: data.purchaseDate,
      warranty_months: data.warrantyMonths,
      warranty_expiry_date: data.warrantyExpiryDate,
      supplier_batch: data.supplierBatch,
      notes: data.notes,
      sold_to: data.soldTo,
      sold_date: data.soldDate,
    });
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

  // ----- Contact-lens (CL) inventory ---------------------------------------
  // Grouped CL stock by brand x power x base-curve x modality, with on-hand
  // qty, nearest expiry, pack info + batch. Store-scoped + fail-soft backend.
  getContactLensInventory: async (
    storeId?: string,
    filters?: {
      brand?: string;
      modality?: string;
      base_curve?: number;
      cl_power?: number;
      near_expiry_days?: number;
    }
  ): Promise<CLInventoryResponse> => {
    const response = await api.get('/inventory/contact-lenses', {
      params: { store_id: storeId, ...filters },
    });
    return response.data as CLInventoryResponse;
  },

  // Expired / expiring-soon / safe buckets + FEFO pick suggestion.
  getContactLensExpiryStatus: async (
    storeId?: string,
    expiringWithinDays: number = 90
  ): Promise<CLExpiryStatusResponse> => {
    const response = await api.get('/inventory/contact-lenses/expiry-status', {
      params: { store_id: storeId, expiring_within_days: expiringWithinDays },
    });
    return response.data as CLExpiryStatusResponse;
  },

  // Opening-stock importer (go-live). Dry-run preview, then commit. The active
  // store is taken from the caller's token server-side.
  previewOpeningStock: async (
    rows: OpeningStockInputRow[],
    skipIfExisting = true,
  ): Promise<OpeningStockResponse> => {
    const response = await api.post('/inventory/opening-stock/preview', {
      rows,
      skip_if_existing: skipIfExisting,
    });
    return response.data as OpeningStockResponse;
  },

  commitOpeningStock: async (
    rows: OpeningStockInputRow[],
    skipIfExisting = true,
  ): Promise<OpeningStockResponse> => {
    const response = await api.post('/inventory/opening-stock/commit', {
      rows,
      skip_if_existing: skipIfExisting,
    });
    return response.data as OpeningStockResponse;
  },

  // ----- F21: defective quarantine lifecycle -------------------------------
  // Mark a physical unit QUARANTINED (pull it off the sellable floor).
  quarantineStock: async (
    stockId: string,
    body: { reason: string; notes?: string; rtv_vendor_id?: string },
  ) => {
    const response = await api.patch(`/inventory/stock/${stockId}/quarantine`, body);
    return response.data;
  },

  // Lift a quarantine (mis-quarantine correction) -- mandatory reason.
  liftQuarantine: async (stockId: string, liftReason: string) => {
    const response = await api.patch(`/inventory/stock/${stockId}/lift-quarantine`, {
      lift_reason: liftReason,
    });
    return response.data;
  },

  // The Quarantine Queue: all QUARANTINED units for the store + unlabeled count.
  getQuarantinedStock: async (params?: {
    store_id?: string;
    rtv_vendor_id?: string;
    label_printed?: boolean;
  }): Promise<QuarantineQueueResponse> => {
    const response = await api.get('/inventory/stock/quarantined', { params });
    return response.data as QuarantineQueueResponse;
  },

  // Build + register the red "DO NOT SHELVE" label and flip the printed flag.
  printQuarantineLabel: async (stockId: string): Promise<QuarantineLabel> => {
    const response = await api.post(`/labels/quarantine/${stockId}`);
    return response.data as QuarantineLabel;
  },
};

export interface QuarantineUnit {
  stock_id: string;
  product_id?: string;
  product_name?: string;
  brand?: string;
  category?: string;
  barcode?: string;
  status?: string;
  quarantine_reason?: string;
  quarantine_at?: string;
  quarantine_by_name?: string;
  quarantine_notes?: string;
  quarantine_label_printed?: boolean;
  rtv_vendor_id?: string;
  /** Resolved RTV vendor display name (router enrichment); falls back to id. */
  rtv_vendor_name?: string;
}

export interface QuarantineQueueResponse {
  items: QuarantineUnit[];
  total: number;
  unlabeled_count: number;
}

export interface QuarantineLabel {
  ok: boolean;
  label_type: string;
  header: string;
  sub_header?: string;
  background_color?: string;
  stock_id: string;
  barcode_value: string;
  name?: string;
  brand?: string;
  category?: string;
  quarantine_reason?: string;
  quarantine_at?: string;
  quarantine_by_name?: string;
  store_name?: string;
  store_code?: string;
  store_brand?: string;
  rtv_vendor_id?: string;
  luxury_brand_line?: string;
}

// One opening-stock import row (product identified by product_id OR sku).
export interface OpeningStockInputRow {
  product_id?: string;
  sku?: string;
  quantity: number;
  location_code?: string;
  batch_code?: string;
  expiry_date?: string;
}

export interface OpeningStockResultRow {
  index: number;
  status:
    | 'WILL_ADD'
    | 'WILL_ADD_ON_TOP'
    | 'SKIP_EXISTING'
    | 'ADDED'
    | 'SKIPPED'
    | 'ERROR';
  product_id?: string;
  sku?: string;
  name?: string;
  identifier?: string;
  quantity?: number;
  existing?: number;
  added?: number;
  message: string;
}

export interface OpeningStockResponse {
  rows: OpeningStockResultRow[];
  summary: {
    total_rows: number;
    units_to_add?: number;
    rows_to_skip?: number;
    units_added?: number;
    rows_skipped?: number;
    rows_with_errors: number;
    skip_if_existing?: boolean;
  };
}

// Contact-lens inventory line (one SKU x batch group).
export interface CLInventoryLine {
  product_id: string;
  sku: string;
  brand: string;
  model: string;
  category: string;
  cl_series: string | null;
  modality: string | null;
  base_curve: number | null;
  diameter: number | null;
  cl_power: number | null;
  cl_cyl: number | null;
  cl_axis: number | null;
  cl_add: number | null;
  color: string | null;
  pack_size: number | null;
  batch_code: string | null;
  expiry_date: string | null;
  location_code: string | null;
  on_hand: number;
  days_until_expiry: number | null;
}

export interface CLInventoryResponse {
  items: CLInventoryLine[];
  total_lines: number;
  total_units: number;
  store_id: string | null;
}

export interface CLExpiryStatusResponse {
  expired: CLInventoryLine[];
  expiring_soon: CLInventoryLine[];
  safe: CLInventoryLine[];
  fefo_pick: CLInventoryLine[];
  near_expiry_days: number;
  summary: {
    expired_count: number;
    expiring_soon_count: number;
    safe_count: number;
    undated_count: number;
  };
}

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

  // PO lifecycle timeline (backend PR #869): chronological owner-vocabulary
  // events (Ordered / Sent / Box received / On shelf / Bill settled) plus the
  // raw linked GRNs + purchase invoices. 404s on cross-store access.
  getPOTimeline: async (poId: string) => {
    const response = await api.get(`/vendors/purchase-orders/${poId}/timeline`);
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
    // F9 — po_id + vendor_invoice_no optional for a Delivery Challan (the tax
    // invoice arrives later); required for a STANDARD GRN (backend-enforced).
    po_id?: string;
    vendor_invoice_no?: string;
    vendor_invoice_date?: string;
    // F9 — Delivery-Challan subtype + fields.
    grn_subtype?: 'STANDARD' | 'DELIVERY_CHALLAN';
    dc_number?: string;
    dc_date?: string;
    vendor_id?: string;
    items: Array<{
      po_item_id?: string;
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

  // F9 — list open (unmatched) Delivery Challans for the bulk DC->invoice tally.
  getOpenDCs: async (params: {
    vendor_id?: string;
    store_id?: string;
    date_from?: string;
    date_to?: string;
  }) => {
    const response = await api.get('/vendors/grn', {
      params: {
        ...params,
        grn_subtype: 'DELIVERY_CHALLAN',
        dc_matched: false,
        status: 'ACCEPTED',
      },
    });
    return response.data;
  },

  // F9 — draft a consolidated invoice from a set of DCs (does not persist).
  draftInvoiceFromDCs: async (dcIds: string[], vendorId?: string) => {
    const response = await api.get('/vendors/purchase-invoices/from-dcs', {
      params: { dc_ids: dcIds.join(','), vendor_id: vendorId },
    });
    return response.data;
  },

  acceptGRN: async (grnId: string) => {
    const response = await api.post(`/vendors/grn/${grnId}/accept`);
    return response.data;
  },

  // Void a PENDING GRN (duplicate/mistake cleanup — no stock was added yet;
  // accepted GRNs must be corrected via a vendor return instead).
  voidGRN: async (grnId: string) => {
    const response = await api.post(`/vendors/grn/${grnId}/void`);
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

  // Vendor portal token (May 2026) — admin generates a signed URL the
  // lens lab opens directly without an IMS user account.
  generatePortalToken: async (vendorId: string, expiresDays?: number) => {
    const response = await api.post(
      `/vendors/${vendorId}/portal-token`,
      expiresDays ? { expires_days: expiresDays } : {},
    );
    return response.data as {
      token_id: string;
      portal_url: string;
      expires_at: string;
      vendor_id: string;
      vendor_name: string;
    };
  },

  listPortalTokens: async (vendorId: string) => {
    const response = await api.get(`/vendors/${vendorId}/portal-tokens`);
    return response.data as {
      tokens: Array<{
        token_id: string;
        created_at: string;
        expires_at: string;
        active: boolean;
        last_used_at: string | null;
      }>;
    };
  },

  revokePortalToken: async (vendorId: string, tokenId: string) => {
    // Backend route is singular `/portal-token/{token_id}` (the list GET is the
    // plural `/portal-tokens`); the plural path here 404'd every revoke.
    const response = await api.delete(
      `/vendors/${vendorId}/portal-token/${tokenId}`,
    );
    return response.data;
  },

  // F8: PO-vs-GRN variance / backorder report. Open/partial PO lines whose
  // ACCEPTED received qty trails the ordered qty, with open qty + aging enum.
  getVarianceReport: async (params?: { store_id?: string; skip?: number; limit?: number }) => {
    const response = await api.get('/vendors/variance-report', { params });
    return response.data as { lines: VarianceLine[]; total: number };
  },

  // F8: dismiss a variance/backorder line with a mandatory justification.
  dismissVariance: async (
    poId: string,
    body: { product_id: string; reason: string; grn_id?: string; bill_id?: string },
  ) => {
    const response = await api.post(`/vendors/purchase-orders/${poId}/dismiss-variance`, body);
    return response.data as {
      dismissed: boolean;
      po_id: string;
      product_id: string;
      vendor_id?: string;
      debit_note_suggested: boolean;
      suggested_amount?: number | null;
    };
  },
};

// F8 variance report row (one open PO line). aging_status is an explicit enum,
// never a colour string.
export interface VarianceLine {
  po_id: string;
  po_number?: string;
  vendor_id?: string;
  vendor_name?: string;
  product_id: string;
  product_name?: string;
  ordered_qty: number;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  open_qty: number;
  variance_status: 'SHORT' | 'OVER' | 'EXACT' | 'UNMATCHED';
  days_overdue: number;
  aging_status: 'ON_TIME' | 'OVERDUE' | 'CRITICALLY_OVERDUE';
  dismissed?: boolean;
  // Server-resolved links for the dismiss flow: the newest ACCEPTED GRN
  // covering this product + the booked invoice (if any). When both are
  // carried on the dismiss call, an over-billed line triggers the
  // debit-note suggestion in the response.
  latest_accepted_grn_id?: string | null;
  booked_bill_id?: string | null;
}

// ============================================================================
// Reorder Settings API (per-product reorder configuration)
// ============================================================================

export const reorderApi = {
  updateReorderSettings: async (
    productId: string,
    settings: {
      reorder_point: number;
      reorder_quantity: number;
      max_stock: number;
      lead_time_days: number;
    }
  ) => {
    // Persist reorder settings via the SINGLE validated product-update path
    // (`PUT /products/{id}` in routers/products.py). Previously this hit the
    // now-retired, unvalidated `PUT /admin/products/{id}` -- consolidated so
    // there is exactly one validated writer to the `products` collection.
    // reorder_point / reorder_quantity / max_stock / lead_time_days are
    // explicit optional fields on the backend ProductUpdate schema.
    const response = await api.put(`/products/${productId}`, settings);
    return response.data;
  },
};

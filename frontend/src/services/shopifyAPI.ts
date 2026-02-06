// ============================================================================
// IMS 2.0 - Shopify API Integration Service
// ============================================================================
// Complete Shopify REST Admin API integration for multi-store setup

import axios, { AxiosInstance } from 'axios';

export interface ShopifyConfig {
  id: string;
  name: string;
  shopDomain: string; // e.g., "your-store.myshopify.com"
  accessToken: string; // Admin API access token
  apiVersion: string; // e.g., "2024-01"
  isActive: boolean;
}

interface ShopifyProduct {
  id: number;
  title: string;
  body_html: string;
  vendor: string;
  product_type: string;
  tags: string;
  variants: ShopifyVariant[];
  images: Array<{ src: string }>;
  status: 'active' | 'draft' | 'archived';
}

interface ShopifyVariant {
  id: number;
  product_id: number;
  title: string;
  sku: string;
  price: string;
  compare_at_price: string | null;
  inventory_quantity: number;
  inventory_item_id: number;
  barcode: string | null;
}

interface ShopifyOrder {
  id: number;
  order_number: number;
  email: string;
  created_at: string;
  total_price: string;
  subtotal_price: string;
  total_tax: string;
  financial_status: string;
  fulfillment_status: string | null;
  customer: ShopifyCustomer;
  line_items: ShopifyLineItem[];
  shipping_address: ShopifyAddress;
  billing_address: ShopifyAddress;
}

interface ShopifyLineItem {
  id: number;
  variant_id: number;
  title: string;
  quantity: number;
  sku: string;
  price: string;
}

interface ShopifyCustomer {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  phone: string | null;
  total_spent: string;
  orders_count: number;
  tags: string;
  default_address: ShopifyAddress;
}

interface ShopifyAddress {
  first_name: string;
  last_name: string;
  address1: string;
  address2: string | null;
  city: string;
  province: string;
  country: string;
  zip: string;
  phone: string | null;
}

interface InventoryLevel {
  inventory_item_id: number;
  location_id: number;
  available: number;
}

class ShopifyAPIService {
  private clients: Map<string, AxiosInstance> = new Map();
  private configs: Map<string, ShopifyConfig> = new Map();

  /**
   * Initialize Shopify API client for a store
   */
  initializeStore(config: ShopifyConfig): void {
    const client = axios.create({
      baseURL: `https://${config.shopDomain}/admin/api/${config.apiVersion}`,
      headers: {
        'X-Shopify-Access-Token': config.accessToken,
        'Content-Type': 'application/json',
      },
      timeout: 30000,
    });

    this.clients.set(config.id, client);
    this.configs.set(config.id, config);
  }

  /**
   * Get Shopify client for a specific store
   */
  private getClient(storeId: string): AxiosInstance {
    const client = this.clients.get(storeId);
    if (!client) {
      throw new Error(`Shopify store ${storeId} not initialized`);
    }
    return client;
  }

  // ============================================================================
  // PRODUCTS API
  // ============================================================================

  /**
   * Fetch all products from Shopify
   */
  async fetchProducts(storeId: string, limit: number = 250): Promise<ShopifyProduct[]> {
    const client = this.getClient(storeId);
    const allProducts: ShopifyProduct[] = [];
    let sinceId: number | null = null;

    do {
      const params: any = { limit };
      if (sinceId) params.since_id = sinceId;

      const response = await client.get('/products.json', { params });
      const products = response.data.products;

      allProducts.push(...products);

      if (products.length < limit) {
        break;
      }
      sinceId = products[products.length - 1].id;
    } while (true);

    return allProducts;
  }

  /**
   * Create a product in Shopify
   */
  async createProduct(storeId: string, productData: any): Promise<ShopifyProduct> {
    const client = this.getClient(storeId);
    const response = await client.post('/products.json', { product: productData });
    return response.data.product;
  }

  /**
   * Update a product in Shopify
   */
  async updateProduct(
    storeId: string,
    productId: number,
    productData: any
  ): Promise<ShopifyProduct> {
    const client = this.getClient(storeId);
    const response = await client.put(`/products/${productId}.json`, { product: productData });
    return response.data.product;
  }

  /**
   * Delete a product from Shopify
   */
  async deleteProduct(storeId: string, productId: number): Promise<void> {
    const client = this.getClient(storeId);
    await client.delete(`/products/${productId}.json`);
  }

  // ============================================================================
  // INVENTORY API
  // ============================================================================

  /**
   * Get inventory levels for a location
   */
  async getInventoryLevels(
    storeId: string,
    locationId: number
  ): Promise<InventoryLevel[]> {
    const client = this.getClient(storeId);
    const allLevels: InventoryLevel[] = [];
    let pageInfo: string | null = null;

    do {
      const params: any = { location_ids: locationId, limit: 250 };
      if (pageInfo) params.page_info = pageInfo;

      const response = await client.get('/inventory_levels.json', { params });
      const levels = response.data.inventory_levels;

      allLevels.push(...levels);

      // Check for pagination
      const linkHeader = response.headers['link'];
      if (linkHeader && linkHeader.includes('rel="next"')) {
        const match = linkHeader.match(/page_info=([^&>]+)/);
        pageInfo = match ? match[1] : null;
      } else {
        pageInfo = null;
      }
    } while (pageInfo);

    return allLevels;
  }

  /**
   * Update inventory level
   */
  async updateInventoryLevel(
    storeId: string,
    inventoryItemId: number,
    locationId: number,
    availableQuantity: number
  ): Promise<void> {
    const client = this.getClient(storeId);
    await client.post('/inventory_levels/set.json', {
      inventory_item_id: inventoryItemId,
      location_id: locationId,
      available: availableQuantity,
    });
  }

  /**
   * Adjust inventory level (add or subtract)
   */
  async adjustInventoryLevel(
    storeId: string,
    inventoryItemId: number,
    locationId: number,
    adjustmentQuantity: number
  ): Promise<void> {
    const client = this.getClient(storeId);
    await client.post('/inventory_levels/adjust.json', {
      inventory_item_id: inventoryItemId,
      location_id: locationId,
      available_adjustment: adjustmentQuantity,
    });
  }

  // ============================================================================
  // ORDERS API
  // ============================================================================

  /**
   * Fetch orders from Shopify
   */
  async fetchOrders(
    storeId: string,
    status: 'open' | 'closed' | 'cancelled' | 'any' = 'any',
    createdAfter?: string
  ): Promise<ShopifyOrder[]> {
    const client = this.getClient(storeId);
    const allOrders: ShopifyOrder[] = [];
    let sinceId: number | null = null;

    do {
      const params: any = { limit: 250, status };
      if (sinceId) params.since_id = sinceId;
      if (createdAfter) params.created_at_min = createdAfter;

      const response = await client.get('/orders.json', { params });
      const orders = response.data.orders;

      allOrders.push(...orders);

      if (orders.length < 250) {
        break;
      }
      sinceId = orders[orders.length - 1].id;
    } while (true);

    return allOrders;
  }

  /**
   * Get a specific order
   */
  async getOrder(storeId: string, orderId: number): Promise<ShopifyOrder> {
    const client = this.getClient(storeId);
    const response = await client.get(`/orders/${orderId}.json`);
    return response.data.order;
  }

  /**
   * Update order fulfillment status
   */
  async createFulfillment(
    storeId: string,
    orderId: number,
    lineItems: Array<{ id: number; quantity: number }>,
    trackingNumber?: string,
    trackingUrl?: string
  ): Promise<void> {
    const client = this.getClient(storeId);
    await client.post(`/orders/${orderId}/fulfillments.json`, {
      fulfillment: {
        line_items: lineItems,
        tracking_number: trackingNumber,
        tracking_url: trackingUrl,
        notify_customer: true,
      },
    });
  }

  // ============================================================================
  // CUSTOMERS API
  // ============================================================================

  /**
   * Fetch customers from Shopify
   */
  async fetchCustomers(storeId: string, limit: number = 250): Promise<ShopifyCustomer[]> {
    const client = this.getClient(storeId);
    const allCustomers: ShopifyCustomer[] = [];
    let sinceId: number | null = null;

    do {
      const params: any = { limit };
      if (sinceId) params.since_id = sinceId;

      const response = await client.get('/customers.json', { params });
      const customers = response.data.customers;

      allCustomers.push(...customers);

      if (customers.length < limit) {
        break;
      }
      sinceId = customers[customers.length - 1].id;
    } while (true);

    return allCustomers;
  }

  /**
   * Create a customer in Shopify
   */
  async createCustomer(storeId: string, customerData: any): Promise<ShopifyCustomer> {
    const client = this.getClient(storeId);
    const response = await client.post('/customers.json', { customer: customerData });
    return response.data.customer;
  }

  /**
   * Update a customer in Shopify
   */
  async updateCustomer(
    storeId: string,
    customerId: number,
    customerData: any
  ): Promise<ShopifyCustomer> {
    const client = this.getClient(storeId);
    const response = await client.put(`/customers/${customerId}.json`, { customer: customerData });
    return response.data.customer;
  }

  // ============================================================================
  // LOCATIONS API
  // ============================================================================

  /**
   * Get all locations (warehouses/stores)
   */
  async getLocations(storeId: string): Promise<Array<{ id: number; name: string }>> {
    const client = this.getClient(storeId);
    const response = await client.get('/locations.json');
    return response.data.locations;
  }

  // ============================================================================
  // WEBHOOKS API
  // ============================================================================

  /**
   * Create a webhook
   */
  async createWebhook(
    storeId: string,
    topic: string,
    address: string
  ): Promise<void> {
    const client = this.getClient(storeId);
    await client.post('/webhooks.json', {
      webhook: {
        topic,
        address,
        format: 'json',
      },
    });
  }

  /**
   * List all webhooks
   */
  async listWebhooks(storeId: string): Promise<any[]> {
    const client = this.getClient(storeId);
    const response = await client.get('/webhooks.json');
    return response.data.webhooks;
  }

  /**
   * Delete a webhook
   */
  async deleteWebhook(storeId: string, webhookId: number): Promise<void> {
    const client = this.getClient(storeId);
    await client.delete(`/webhooks/${webhookId}.json`);
  }
}

// Export singleton instance
export const shopifyAPI = new ShopifyAPIService();

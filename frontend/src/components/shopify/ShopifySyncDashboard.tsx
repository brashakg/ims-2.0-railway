// ============================================================================
// IMS 2.0 - Shopify Sync Dashboard
// ============================================================================
// Manage multi-store Shopify integration and syncing

import { useState, useEffect } from 'react';
import {
  RefreshCw,
  AlertTriangle,
  Package,
  ShoppingCart,
  Users,
  Boxes,
  TrendingUp,
  Loader2,
  Plus,
  Link as LinkIcon,
  Activity,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { shopifyAPI, type ShopifyConfig } from '../../services/shopifyAPI';

interface SyncStatus {
  storeId: string;
  storeName: string;
  lastSync: {
    products?: string;
    inventory?: string;
    orders?: string;
    customers?: string;
  };
  counts: {
    products: number;
    orders: number;
    customers: number;
    pendingOrders: number;
  };
  isActive: boolean;
  isSyncing: boolean;
}

export function ShopifySyncDashboard() {
  const toast = useToast();

  const [stores, setStores] = useState<ShopifyConfig[]>([]);
  const [syncStatus, setSyncStatus] = useState<SyncStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [_showAddStore, setShowAddStore] = useState(false);

  useEffect(() => {
    loadStores();
  }, []);

  const loadStores = async () => {
    setIsLoading(true);
    try {
      // In production, fetch from backend API
      const mockStores: ShopifyConfig[] = [
        {
          id: 'store-1',
          name: 'Better Vision Main Store',
          shopDomain: 'bettervision-main.myshopify.com',
          accessToken: 'shpat_xxxxx',
          apiVersion: '2024-01',
          isActive: true,
        },
        {
          id: 'store-2',
          name: 'Better Vision Wholesale',
          shopDomain: 'bettervision-wholesale.myshopify.com',
          accessToken: 'shpat_yyyyy',
          apiVersion: '2024-01',
          isActive: true,
        },
        {
          id: 'store-3',
          name: 'Better Vision B2B',
          shopDomain: 'bettervision-b2b.myshopify.com',
          accessToken: 'shpat_zzzzz',
          apiVersion: '2024-01',
          isActive: true,
        },
      ];

      setStores(mockStores);

      // Initialize all stores
      mockStores.forEach(store => {
        shopifyAPI.initializeStore(store);
      });

      // Load sync status
      const mockSyncStatus: SyncStatus[] = mockStores.map(store => ({
        storeId: store.id,
        storeName: store.name,
        lastSync: {
          products: '2025-02-06T10:30:00Z',
          inventory: '2025-02-06T11:15:00Z',
          orders: '2025-02-06T11:45:00Z',
          customers: '2025-02-06T09:00:00Z',
        },
        counts: {
          products: 1250,
          orders: 450,
          customers: 890,
          pendingOrders: 12,
        },
        isActive: store.isActive,
        isSyncing: false,
      }));

      setSyncStatus(mockSyncStatus);
    } catch (error: any) {
      toast.error('Failed to load Shopify stores');
    } finally {
      setIsLoading(false);
    }
  };

  const syncProducts = async (storeId: string) => {
    toast.info('Starting product sync...');

    // Update syncing status
    setSyncStatus(prev =>
      prev.map(s =>
        s.storeId === storeId ? { ...s, isSyncing: true } : s
      )
    );

    try {
      // Fetch products from Shopify
      const products = await shopifyAPI.fetchProducts(storeId);

      // In production, save to your database via API
      console.log(`Fetched ${products.length} products from ${storeId}`);

      // Update last sync time
      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId
            ? {
                ...s,
                lastSync: { ...s.lastSync, products: new Date().toISOString() },
                counts: { ...s.counts, products: products.length },
                isSyncing: false,
              }
            : s
        )
      );

      toast.success(`Successfully synced ${products.length} products!`);
    } catch (error: any) {
      toast.error(`Product sync failed: ${error.message}`);
      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId ? { ...s, isSyncing: false } : s
        )
      );
    }
  };

  const syncInventory = async (storeId: string) => {
    toast.info('Starting inventory sync...');

    setSyncStatus(prev =>
      prev.map(s =>
        s.storeId === storeId ? { ...s, isSyncing: true } : s
      )
    );

    try {
      // Get locations for the store
      const locations = await shopifyAPI.getLocations(storeId);

      if (locations.length === 0) {
        throw new Error('No locations found');
      }

      // Fetch inventory levels for the primary location
      const primaryLocation = locations[0];
      const inventoryLevels = await shopifyAPI.getInventoryLevels(storeId, primaryLocation.id);

      // In production, update your database
      console.log(`Fetched ${inventoryLevels.length} inventory items from ${storeId}`);

      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId
            ? {
                ...s,
                lastSync: { ...s.lastSync, inventory: new Date().toISOString() },
                isSyncing: false,
              }
            : s
        )
      );

      toast.success(`Successfully synced ${inventoryLevels.length} inventory items!`);
    } catch (error: any) {
      toast.error(`Inventory sync failed: ${error.message}`);
      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId ? { ...s, isSyncing: false } : s
        )
      );
    }
  };

  const syncOrders = async (storeId: string) => {
    toast.info('Starting order sync...');

    setSyncStatus(prev =>
      prev.map(s =>
        s.storeId === storeId ? { ...s, isSyncing: true } : s
      )
    );

    try {
      // Fetch recent orders (last 7 days)
      const sevenDaysAgo = new Date();
      sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

      const orders = await shopifyAPI.fetchOrders(
        storeId,
        'any',
        sevenDaysAgo.toISOString()
      );

      // In production, save to your database
      console.log(`Fetched ${orders.length} orders from ${storeId}`);

      const pendingOrders = orders.filter(
        o => o.fulfillment_status === null || o.fulfillment_status === 'partial'
      ).length;

      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId
            ? {
                ...s,
                lastSync: { ...s.lastSync, orders: new Date().toISOString() },
                counts: { ...s.counts, orders: orders.length, pendingOrders },
                isSyncing: false,
              }
            : s
        )
      );

      toast.success(`Successfully synced ${orders.length} orders!`);
    } catch (error: any) {
      toast.error(`Order sync failed: ${error.message}`);
      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId ? { ...s, isSyncing: false } : s
        )
      );
    }
  };

  const syncCustomers = async (storeId: string) => {
    toast.info('Starting customer sync...');

    setSyncStatus(prev =>
      prev.map(s =>
        s.storeId === storeId ? { ...s, isSyncing: true } : s
      )
    );

    try {
      const customers = await shopifyAPI.fetchCustomers(storeId);

      // In production, save to your database
      console.log(`Fetched ${customers.length} customers from ${storeId}`);

      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId
            ? {
                ...s,
                lastSync: { ...s.lastSync, customers: new Date().toISOString() },
                counts: { ...s.counts, customers: customers.length },
                isSyncing: false,
              }
            : s
        )
      );

      toast.success(`Successfully synced ${customers.length} customers!`);
    } catch (error: any) {
      toast.error(`Customer sync failed: ${error.message}`);
      setSyncStatus(prev =>
        prev.map(s =>
          s.storeId === storeId ? { ...s, isSyncing: false } : s
        )
      );
    }
  };

  const syncAll = async (storeId: string) => {
    await syncProducts(storeId);
    await syncInventory(storeId);
    await syncOrders(storeId);
    await syncCustomers(storeId);
  };

  const formatLastSync = (timestamp?: string) => {
    if (!timestamp) return 'Never';
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <LinkIcon className="w-7 h-7 text-green-600" />
            Shopify Integration
          </h1>
          <p className="text-gray-500 mt-1">
            Manage multi-store Shopify synchronization
          </p>
        </div>
        <button
          onClick={() => setShowAddStore(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          Add Store
        </button>
      </div>

      {/* Overview Stats */}
      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <Activity className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Active Stores</p>
              <p className="text-2xl font-bold text-gray-900">
                {stores.filter(s => s.isActive).length}
              </p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Package className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Products</p>
              <p className="text-2xl font-bold text-gray-900">
                {syncStatus.reduce((sum, s) => sum + s.counts.products, 0)}
              </p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <ShoppingCart className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Pending Orders</p>
              <p className="text-2xl font-bold text-gray-900">
                {syncStatus.reduce((sum, s) => sum + s.counts.pendingOrders, 0)}
              </p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <Users className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Customers</p>
              <p className="text-2xl font-bold text-gray-900">
                {syncStatus.reduce((sum, s) => sum + s.counts.customers, 0)}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Store Cards */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : (
        <div className="space-y-4">
          {syncStatus.map((status) => {
            const store = stores.find(s => s.id === status.storeId);
            if (!store) return null;

            return (
              <div key={status.storeId} className="card">
                {/* Store Header */}
                <div className="flex items-center justify-between mb-4 pb-4 border-b border-gray-200">
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-3 h-3 rounded-full ${
                        status.isActive ? 'bg-green-500' : 'bg-gray-300'
                      }`}
                    />
                    <div>
                      <h3 className="text-lg font-bold text-gray-900">{status.storeName}</h3>
                      <p className="text-sm text-gray-500">{store.shopDomain}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => syncAll(status.storeId)}
                      disabled={status.isSyncing}
                      className="btn-primary text-sm flex items-center gap-2"
                    >
                      {status.isSyncing ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Syncing...
                        </>
                      ) : (
                        <>
                          <RefreshCw className="w-4 h-4" />
                          Sync All
                        </>
                      )}
                    </button>
                  </div>
                </div>

                {/* Sync Actions Grid */}
                <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
                  {/* Products */}
                  <div className="p-4 bg-blue-50 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Package className="w-5 h-5 text-blue-600" />
                        <span className="font-medium text-gray-900">Products</span>
                      </div>
                      <span className="text-2xl font-bold text-blue-600">
                        {status.counts.products}
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      Last sync: {formatLastSync(status.lastSync.products)}
                    </p>
                    <button
                      onClick={() => syncProducts(status.storeId)}
                      disabled={status.isSyncing}
                      className="w-full btn-outline text-sm"
                    >
                      Sync Products
                    </button>
                  </div>

                  {/* Inventory */}
                  <div className="p-4 bg-green-50 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Boxes className="w-5 h-5 text-green-600" />
                        <span className="font-medium text-gray-900">Inventory</span>
                      </div>
                      <TrendingUp className="w-5 h-5 text-green-600" />
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      Last sync: {formatLastSync(status.lastSync.inventory)}
                    </p>
                    <button
                      onClick={() => syncInventory(status.storeId)}
                      disabled={status.isSyncing}
                      className="w-full btn-outline text-sm"
                    >
                      Sync Inventory
                    </button>
                  </div>

                  {/* Orders */}
                  <div className="p-4 bg-purple-50 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <ShoppingCart className="w-5 h-5 text-purple-600" />
                        <span className="font-medium text-gray-900">Orders</span>
                      </div>
                      <div className="text-right">
                        <span className="text-2xl font-bold text-purple-600">
                          {status.counts.pendingOrders}
                        </span>
                        <p className="text-xs text-gray-600">pending</p>
                      </div>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      Last sync: {formatLastSync(status.lastSync.orders)}
                    </p>
                    <button
                      onClick={() => syncOrders(status.storeId)}
                      disabled={status.isSyncing}
                      className="w-full btn-outline text-sm"
                    >
                      Sync Orders
                    </button>
                  </div>

                  {/* Customers */}
                  <div className="p-4 bg-orange-50 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Users className="w-5 h-5 text-orange-600" />
                        <span className="font-medium text-gray-900">Customers</span>
                      </div>
                      <span className="text-2xl font-bold text-orange-600">
                        {status.counts.customers}
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      Last sync: {formatLastSync(status.lastSync.customers)}
                    </p>
                    <button
                      onClick={() => syncCustomers(status.storeId)}
                      disabled={status.isSyncing}
                      className="w-full btn-outline text-sm"
                    >
                      Sync Customers
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Info Banner */}
      <div className="card bg-blue-50 border-blue-200">
        <div className="flex gap-3">
          <AlertTriangle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-blue-900">
            <p className="font-medium mb-1">Shopify Integration Tips</p>
            <ul className="list-disc list-inside space-y-1 text-blue-800">
              <li>Inventory syncs are bi-directional - changes in IMS update Shopify</li>
              <li>Orders are synced automatically via webhooks in real-time</li>
              <li>Products can be pushed from IMS to all stores simultaneously</li>
              <li>Customer data syncs preserve loyalty points and purchase history</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

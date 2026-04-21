// ============================================================================
// IMS 2.0 - Settings: Store, Category, Brand & Discount Management
// ============================================================================

import { useState, useEffect } from 'react';
import {
  Store, Tag, Plus, Edit2, Trash2, X, Check,
  ToggleLeft, ToggleRight, Boxes,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import {
  adminStoreApi,
  adminBrandApi,
  adminDiscountApi,
} from '../../services/api';
import type { StoreData, Category, Brand } from './settingsTypes';
import { CATEGORY_DEFINITIONS } from './settingsTypes';

// ============================================================================
// Transform helpers
// ============================================================================

const transformStore = (s: any): StoreData => ({
  id: s.id || s.store_id || s._id,
  storeCode: s.store_code || s.storeCode || '',
  storeName: s.store_name || s.storeName || s.name || '',
  brand: s.brand || 'BETTER_VISION',
  gstin: s.gstin || s.GSTIN || '',
  address: s.address || '',
  city: s.city || '',
  state: s.state || '',
  pincode: s.pincode || s.postal_code || '',
  phone: s.phone || s.contact_phone || '',
  email: s.email || s.contact_email || '',
  openingTime: s.opening_time || s.openingTime || '10:00',
  closingTime: s.closing_time || s.closingTime || '20:00',
  geoLat: s.geo_lat || s.latitude,
  geoLng: s.geo_lng || s.longitude,
  geoFenceRadius: s.geo_fence_radius || s.geoFenceRadius || 100,
  enabledCategories: s.enabled_categories || s.enabledCategories || CATEGORY_DEFINITIONS.map(c => c.code),
  isActive: s.is_active !== false,
});

const transformBrand = (b: any): Brand => ({
  id: b.id || b.brand_id || b._id,
  brandName: b.brand_name || b.brandName || b.name || '',
  brandCode: b.brand_code || b.brandCode || b.code || '',
  categories: b.categories || [],
  tier: b.tier || 'MASS',
  isActive: b.is_active !== false,
  subbrands: (b.subbrands || []).map((sb: any) => ({
    id: sb.id || sb.subbrand_id,
    name: sb.name || sb.subbrand_name,
    code: sb.code || sb.subbrand_code,
    brandId: b.id,
    isActive: sb.is_active !== false,
  })),
});

// ============================================================================
// Store Management Section
// ============================================================================

export function StoreManagementSection() {
  const toast = useToast();
  const [isLoading, setIsLoading] = useState(false);
  const [stores, setStores] = useState<StoreData[]>([]);
  const [showAddStoreModal, setShowAddStoreModal] = useState(false);
  const [editingStore, setEditingStore] = useState<StoreData | null>(null);
  const categories = CATEGORY_DEFINITIONS;

  useEffect(() => {
    loadStores();
  }, []);

  const loadStores = async () => {
    setIsLoading(true);
    try {
      const storesResponse = await adminStoreApi.getStores();
      if (storesResponse?.stores) {
        setStores(storesResponse.stores.map(transformStore));
      } else if (Array.isArray(storesResponse)) {
        setStores(storesResponse.map(transformStore));
      }
    } catch {
      setStores([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveStore = async (storeData: Partial<StoreData>) => {
    try {
      setIsLoading(true);
      const apiData = {
        name: storeData.storeName || '',
        code: storeData.storeCode || '',
        address: storeData.address || '',
        city: storeData.city || '',
        state: storeData.state || '',
        phone: storeData.phone || '',
        email: storeData.email || '',
        gst: storeData.gstin || '',
        pincode: storeData.pincode || '',
        opening_time: storeData.openingTime || '10:00',
        closing_time: storeData.closingTime || '20:00',
        geo_fence_radius: storeData.geoFenceRadius || 100,
        enabled_categories: storeData.enabledCategories || [],
        status: storeData.isActive ? 'ACTIVE' : 'INACTIVE',
      };

      if (editingStore?.id) {
        await adminStoreApi.updateStore(editingStore.id, apiData);
      } else {
        await adminStoreApi.createStore(apiData);
      }
      toast.success(editingStore ? 'Store updated successfully' : 'Store created successfully');
      setShowAddStoreModal(false);
      setEditingStore(null);
      loadStores();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save store');
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteStore = async (storeId: string) => {
    if (!window.confirm('Are you sure you want to delete this store?')) return;
    try {
      await adminStoreApi.deleteStore(storeId);
      toast.success('Store deleted successfully');
      loadStores();
    } catch {
      toast.error('Failed to delete store');
    }
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-48"><div className="text-gray-500">Loading...</div></div>;
  }

  return (
    <>
      <div className="space-y-4">
        <div className="card">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold text-gray-900">Store Management</h2>
            <button
              onClick={() => setShowAddStoreModal(true)}
              className="btn-primary flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Add Store
            </button>
          </div>

          {stores.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Store className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No stores created yet</p>
              <p className="text-sm">Click "Add Store" to create your first store</p>
            </div>
          ) : (
            <div className="grid gap-4">
              {stores.map(store => (
                <div
                  key={store.id}
                  className="p-4 border border-gray-200 rounded-lg hover:border-bv-red-200 transition-colors"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-gray-900">{store.storeName}</h3>
                        <span className="text-xs bg-gray-100 px-2 py-0.5 rounded">{store.storeCode}</span>
                        {store.isActive ? (
                          <span className="badge-success">Active</span>
                        ) : (
                          <span className="badge-error">Inactive</span>
                        )}
                      </div>
                      <p className="text-sm text-gray-500 mt-1">{store.address}, {store.city}</p>
                      <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                        <span>GSTIN: {store.gstin || 'Not set'}</span>
                        <span>Hours: {store.openingTime} - {store.closingTime}</span>
                        <span>Geo-fence: {store.geoFenceRadius}m</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => {
                          setEditingStore(store);
                          setShowAddStoreModal(true);
                        }}
                        className="p-2 text-gray-500 hover:text-bv-red-600 hover:bg-gray-100 rounded"
                        title="Edit store"
                      >
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDeleteStore(store.id)}
                        className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded"
                        title="Delete store"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Store Modal */}
      {showAddStoreModal && (
        <StoreModal
          store={editingStore}
          onClose={() => {
            setShowAddStoreModal(false);
            setEditingStore(null);
          }}
          onSave={handleSaveStore}
          categories={categories}
        />
      )}
    </>
  );
}

// ============================================================================
// Category Section
// ============================================================================

export function CategorySection() {
  const categories = CATEGORY_DEFINITIONS;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Category Master</h2>
          <p className="text-sm text-gray-500">Product categories with HSN codes and attributes</p>
        </div>
      </div>

      <div className="space-y-3">
        {categories.map(cat => (
          <div
            key={cat.code}
            className="p-4 border border-gray-200 rounded-lg"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className={clsx(
                  'w-10 h-10 rounded-lg flex items-center justify-center',
                  cat.isActive ? 'bg-blue-50' : 'bg-gray-100'
                )}>
                  <Tag className={clsx('w-5 h-5', cat.isActive ? 'text-blue-600' : 'text-gray-500')} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium text-gray-900">{cat.name}</h3>
                    <span className="text-xs bg-gray-100 px-2 py-0.5 rounded font-mono">{cat.code}</span>
                  </div>
                  <p className="text-xs text-gray-500">
                    HSN: {cat.hsnCode} | GST: {cat.gstRate}%
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                {cat.isActive ? (
                  <ToggleRight className="w-6 h-6 text-green-600 cursor-pointer" />
                ) : (
                  <ToggleLeft className="w-6 h-6 text-gray-500 cursor-pointer" />
                )}
                <button className="text-gray-500 hover:text-bv-red-600">
                  <Edit2 className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Attributes */}
            <div className="mt-3 pt-3 border-t border-gray-200">
              <p className="text-xs text-gray-500 mb-2">Required Attributes:</p>
              <div className="flex flex-wrap gap-1">
                {cat.attributes.map(attr => (
                  <span key={attr} className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                    {attr}
                  </span>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// Brand Section
// ============================================================================

export function BrandSection() {
  const toast = useToast();
  const [isLoading, setIsLoading] = useState(false);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [showAddBrandModal, setShowAddBrandModal] = useState(false);
  const [editingBrand, setEditingBrand] = useState<Brand | null>(null);
  const categories = CATEGORY_DEFINITIONS;

  useEffect(() => {
    loadBrands();
  }, []);

  const loadBrands = async () => {
    setIsLoading(true);
    try {
      const brandsResponse = await adminBrandApi.getBrands();
      if (brandsResponse?.brands) {
        setBrands(brandsResponse.brands.map(transformBrand));
      } else if (Array.isArray(brandsResponse)) {
        setBrands(brandsResponse.map(transformBrand));
      }
    } catch {
      setBrands([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveBrand = async (brandData: Partial<Brand>) => {
    try {
      setIsLoading(true);
      const apiData = {
        name: brandData.brandName || '',
        code: brandData.brandCode || '',
        categories: brandData.categories || [],
        tier: brandData.tier || 'MASS',
        status: brandData.isActive ? 'ACTIVE' : 'INACTIVE',
      };

      if (editingBrand?.id) {
        await adminBrandApi.updateBrand(editingBrand.id, apiData);
      } else {
        await adminBrandApi.createBrand(apiData);
      }
      toast.success(editingBrand ? 'Brand updated successfully' : 'Brand created successfully');
      setShowAddBrandModal(false);
      setEditingBrand(null);
      loadBrands();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save brand');
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteBrand = async (brandId: string) => {
    if (!window.confirm('Are you sure you want to delete this brand?')) return;
    try {
      await adminBrandApi.deleteBrand(brandId);
      toast.success('Brand deleted successfully');
      loadBrands();
    } catch {
      toast.error('Failed to delete brand');
    }
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-48"><div className="text-gray-500">Loading...</div></div>;
  }

  return (
    <>
      <div className="card">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Brand Master</h2>
            <p className="text-sm text-gray-500">Manage brands and subbrands with tier classification</p>
          </div>
          <button
            onClick={() => setShowAddBrandModal(true)}
            className="btn-primary flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add Brand
          </button>
        </div>

        {brands.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <Boxes className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No brands created yet</p>
            <p className="text-sm">Click "Add Brand" to add your first brand</p>
          </div>
        ) : (
          <div className="space-y-3">
            {brands.map(brand => (
              <div
                key={brand.id}
                className="p-4 border border-gray-200 rounded-lg"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="font-medium text-gray-900">{brand.brandName}</h3>
                      <span className="text-xs bg-gray-100 px-2 py-0.5 rounded font-mono">{brand.brandCode}</span>
                      <span className={clsx(
                        'text-xs px-2 py-0.5 rounded',
                        brand.tier === 'LUXURY' ? 'bg-purple-100 text-purple-700' :
                        brand.tier === 'PREMIUM' ? 'bg-blue-100 text-blue-700' :
                        'bg-gray-100 text-gray-700'
                      )}>
                        {brand.tier}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      Categories: {brand.categories.join(', ')}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        setEditingBrand(brand);
                        setShowAddBrandModal(true);
                      }}
                      className="text-gray-500 hover:text-bv-red-600"
                      title="Edit brand"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDeleteBrand(brand.id)}
                      className="text-gray-500 hover:text-red-600"
                      title="Delete brand"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Subbrands */}
                {brand.subbrands && brand.subbrands.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-gray-200">
                    <p className="text-xs text-gray-500 mb-2">Subbrands:</p>
                    <div className="flex flex-wrap gap-2">
                      {brand.subbrands.map(sb => (
                        <span key={sb.id} className="text-xs bg-white px-2 py-1 rounded border">
                          {sb.name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Brand Modal */}
      {showAddBrandModal && (
        <BrandModal
          brand={editingBrand}
          categories={categories}
          onClose={() => {
            setShowAddBrandModal(false);
            setEditingBrand(null);
          }}
          onSave={handleSaveBrand}
        />
      )}
    </>
  );
}

// ============================================================================
// Discount Section
// ============================================================================

export function DiscountSection() {
  const toast = useToast();
  const [discounts, setDiscounts] = useState([
    { role: 'Sales Staff', roleKey: 'SALES_STAFF', mass: 5, premium: 3, luxury: 0 },
    { role: 'Sales Cashier', roleKey: 'SALES_CASHIER', mass: 10, premium: 5, luxury: 3 },
    { role: 'Optometrist', roleKey: 'OPTOMETRIST', mass: 5, premium: 3, luxury: 0 },
    { role: 'Workshop Staff', roleKey: 'WORKSHOP_STAFF', mass: 0, premium: 0, luxury: 0 },
    { role: 'Store Manager', roleKey: 'STORE_MANAGER', mass: 15, premium: 10, luxury: 5 },
    { role: 'Accountant', roleKey: 'ACCOUNTANT', mass: 10, premium: 5, luxury: 3 },
    { role: 'Area Manager', roleKey: 'AREA_MANAGER', mass: 20, premium: 15, luxury: 10 },
    { role: 'Admin', roleKey: 'ADMIN', mass: 100, premium: 100, luxury: 100 },
    { role: 'Superadmin', roleKey: 'SUPERADMIN', mass: 100, premium: 100, luxury: 100 },
  ]);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    loadDiscounts();
  }, []);

  const loadDiscounts = async () => {
    try {
      const [caps] = await Promise.all([
        adminDiscountApi.getRoleDiscountCaps().catch(() => null),
        adminDiscountApi.getTierDiscounts().catch(() => null),
      ]);
      if (caps?.rules) {
        setDiscounts(prev => prev.map(d => {
          const cap = caps.rules[d.roleKey];
          return cap ? { ...d, mass: cap.mass ?? d.mass, premium: cap.premium ?? d.premium, luxury: cap.luxury ?? d.luxury } : d;
        }));
      }
    } catch {
      // Discount API not available
    }
  };

  const handleSaveRules = async () => {
    setIsSaving(true);
    try {
      // Save each role's discount cap
      await Promise.all(
        discounts.map(d =>
          adminDiscountApi.setRoleDiscountCap(d.roleKey, d.mass).catch(() => null)
        )
      );
      toast.success('Discount rules saved');
    } catch {
      toast.error('Failed to save');
    }
    setIsSaving(false);
  };

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Discount Rules</h2>
          <p className="text-sm text-gray-500">Maximum discount by role and brand tier</p>
        </div>
        <button
          onClick={handleSaveRules}
          disabled={isSaving}
          className="btn-outline flex items-center gap-2"
        >
          <Edit2 className="w-4 h-4" />
          {isSaving ? 'Saving...' : 'Save Rules'}
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-white border-b border-gray-200">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Mass</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Premium</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Luxury</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {discounts.map((row, idx) => (
              <tr key={row.roleKey}>
                <td className="px-4 py-3 font-medium">{row.role}</td>
                <td className="px-4 py-3 text-center">
                  <input
                    type="number"
                    value={row.mass}
                    onChange={(e) => {
                      const val = Math.min(100, Math.max(0, parseInt(e.target.value) || 0));
                      setDiscounts(prev => prev.map((d, i) => i === idx ? { ...d, mass: val } : d));
                    }}
                    min="0"
                    max="100"
                    className="w-16 px-2 py-1 text-center border border-gray-200 rounded"
                  />
                  %
                </td>
                <td className="px-4 py-3 text-center">
                  <input
                    type="number"
                    value={row.premium}
                    onChange={(e) => {
                      const val = Math.min(100, Math.max(0, parseInt(e.target.value) || 0));
                      setDiscounts(prev => prev.map((d, i) => i === idx ? { ...d, premium: val } : d));
                    }}
                    min="0"
                    max="100"
                    className="w-16 px-2 py-1 text-center border border-gray-200 rounded"
                  />
                  %
                </td>
                <td className="px-4 py-3 text-center">
                  <input
                    type="number"
                    value={row.luxury}
                    onChange={(e) => {
                      const val = Math.min(100, Math.max(0, parseInt(e.target.value) || 0));
                      setDiscounts(prev => prev.map((d, i) => i === idx ? { ...d, luxury: val } : d));
                    }}
                    min="0"
                    max="100"
                    className="w-16 px-2 py-1 text-center border border-gray-200 rounded"
                  />
                  %
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 pt-6 border-t border-gray-200">
        <h3 className="text-sm font-medium text-gray-700 mb-3">MRP Rules (per SYSTEM_INTENT)</h3>
        <ul className="space-y-2 text-sm text-gray-500">
          <li className="flex items-center gap-2">
            <Check className="w-4 h-4 text-green-600" />
            If Offer Price = MRP -&gt; Store can apply discount up to role cap
          </li>
          <li className="flex items-center gap-2">
            <Check className="w-4 h-4 text-green-600" />
            If Offer Price &lt; MRP -&gt; HQ discount applied, no further discount allowed
          </li>
          <li className="flex items-center gap-2">
            <Check className="w-4 h-4 text-green-600" />
            Discount above cap requires approval from higher role
          </li>
        </ul>
      </div>
    </div>
  );
}

// ============================================================================
// Store Modal
// ============================================================================

function StoreModal({
  store,
  onClose,
  onSave,
  categories,
}: {
  store: StoreData | null;
  onClose: () => void;
  onSave: (data: Partial<StoreData>) => void;
  categories: Category[];
}) {
  const [formData, setFormData] = useState<Partial<StoreData>>(
    store || {
      storeCode: '',
      storeName: '',
      brand: 'BETTER_VISION',
      gstin: '',
      address: '',
      city: '',
      state: '',
      pincode: '',
      phone: '',
      email: '',
      openingTime: '10:00',
      closingTime: '20:00',
      geoFenceRadius: 100,
      enabledCategories: categories.map(c => c.code),
      isActive: true,
    }
  );

  const handleChange = (field: string, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">
            {store ? 'Edit Store' : 'Add New Store'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Store Code *</label>
              <input
                type="text"
                value={formData.storeCode || ''}
                onChange={e => handleChange('storeCode', e.target.value)}
                placeholder="STORE-XXX-01"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Store Name *</label>
              <input
                type="text"
                value={formData.storeName || ''}
                onChange={e => handleChange('storeName', e.target.value)}
                placeholder="Better Vision - Park Street"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">GSTIN</label>
            <input
              type="text"
              value={formData.gstin || ''}
              onChange={e => handleChange('gstin', e.target.value.toUpperCase())}
              placeholder="19ABCDE1234F1Z5"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Address *</label>
            <textarea
              value={formData.address || ''}
              onChange={e => handleChange('address', e.target.value)}
              rows={2}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
            />
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">City *</label>
              <input
                type="text"
                value={formData.city || ''}
                onChange={e => handleChange('city', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">State *</label>
              <input
                type="text"
                value={formData.state || ''}
                onChange={e => handleChange('state', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Pincode *</label>
              <input
                type="text"
                value={formData.pincode || ''}
                onChange={e => handleChange('pincode', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Phone</label>
              <input
                type="tel"
                value={formData.phone || ''}
                onChange={e => handleChange('phone', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                type="email"
                value={formData.email || ''}
                onChange={e => handleChange('email', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Opening Time</label>
              <input
                type="time"
                value={formData.openingTime || '10:00'}
                onChange={e => handleChange('openingTime', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Closing Time</label>
              <input
                type="time"
                value={formData.closingTime || '20:00'}
                onChange={e => handleChange('closingTime', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Geo-fence (meters)</label>
              <input
                type="number"
                value={formData.geoFenceRadius || 100}
                onChange={e => handleChange('geoFenceRadius', parseInt(e.target.value))}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Enabled Categories</label>
            <div className="grid grid-cols-3 gap-2">
              {categories.map(cat => (
                <label key={cat.code} className="flex items-center gap-2 p-2 bg-white rounded cursor-pointer hover:bg-gray-100">
                  <input
                    type="checkbox"
                    checked={formData.enabledCategories?.includes(cat.code) || false}
                    onChange={e => {
                      const current = formData.enabledCategories || [];
                      if (e.target.checked) {
                        handleChange('enabledCategories', [...current, cat.code]);
                      } else {
                        handleChange('enabledCategories', current.filter(c => c !== cat.code));
                      }
                    }}
                    className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                  />
                  <span className="text-sm">{cat.shortName}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
          <button onClick={() => onSave(formData)} className="btn-primary">
            {store ? 'Update Store' : 'Create Store'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Brand Modal
// ============================================================================

function BrandModal({
  brand,
  categories,
  onClose,
  onSave,
}: {
  brand: Brand | null;
  categories: Category[];
  onClose: () => void;
  onSave: (data: Partial<Brand>) => void;
}) {
  const [formData, setFormData] = useState<Partial<Brand>>(
    brand || {
      brandName: '',
      brandCode: '',
      categories: [],
      tier: 'MASS',
      isActive: true,
      subbrands: [],
    }
  );

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">
            {brand ? 'Edit Brand' : 'Add New Brand'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Brand Name *</label>
              <input
                type="text"
                value={formData.brandName || ''}
                onChange={e => setFormData(prev => ({ ...prev, brandName: e.target.value }))}
                placeholder="Ray-Ban"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Brand Code *</label>
              <input
                type="text"
                value={formData.brandCode || ''}
                onChange={e => setFormData(prev => ({ ...prev, brandCode: e.target.value.toUpperCase() }))}
                placeholder="RAYBAN"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Tier *</label>
            <select
              value={formData.tier || 'MASS'}
              onChange={e => setFormData(prev => ({ ...prev, tier: e.target.value as Brand['tier'] }))}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
            >
              <option value="MASS">Mass</option>
              <option value="PREMIUM">Premium</option>
              <option value="LUXURY">Luxury</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Categories *</label>
            <div className="grid grid-cols-3 gap-2">
              {categories.map(cat => (
                <label key={cat.code} className="flex items-center gap-2 p-2 bg-white rounded cursor-pointer hover:bg-gray-100">
                  <input
                    type="checkbox"
                    checked={formData.categories?.includes(cat.code) || false}
                    onChange={e => {
                      const current = formData.categories || [];
                      if (e.target.checked) {
                        setFormData(prev => ({ ...prev, categories: [...current, cat.code] }));
                      } else {
                        setFormData(prev => ({ ...prev, categories: current.filter(c => c !== cat.code) }));
                      }
                    }}
                    className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                  />
                  <span className="text-sm">{cat.shortName}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
          <button onClick={() => onSave(formData)} className="btn-primary">
            {brand ? 'Update Brand' : 'Create Brand'}
          </button>
        </div>
      </div>
    </div>
  );
}

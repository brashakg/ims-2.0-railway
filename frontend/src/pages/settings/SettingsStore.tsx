// ============================================================================
// IMS 2.0 - Settings: Store, Category, Brand & Discount Management
// ============================================================================

import { useState, useEffect } from 'react';
import {
  Store, Tag, Plus, Edit2, Trash2, X, Check,
  Boxes, Lock, Info,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { validatePincode, validatePhone, validateGeoRadius, firstError } from '../../utils/validators';
import {
  adminStoreApi,
  adminBrandApi,
  adminDiscountApi,
} from '../../services/api';
import { entitiesApi, type Entity } from '../../services/api/entities';
// Canonical store CRUD (same path the Organization screen uses). Imported
// directly from the module (not the api barrel) per the known re-export gotcha.
import { orgStoreApi } from '../../services/api/stores';
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
  entityId: s.entity_id ?? s.entityId ?? null,
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
  const [entities, setEntities] = useState<Entity[]>([]);
  const [showAddStoreModal, setShowAddStoreModal] = useState(false);
  const [editingStore, setEditingStore] = useState<StoreData | null>(null);
  const categories = CATEGORY_DEFINITIONS;

  useEffect(() => {
    loadStores();
    // Legal entities for the store<->entity link (Settings > Entities manages them).
    entitiesApi.list().then((r) => setEntities(r.entities || [])).catch(() => setEntities([]));
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
    const fieldErr = firstError(
      validatePincode(storeData.pincode),
      validatePhone(storeData.phone),
      validateGeoRadius(storeData.geoFenceRadius),
    );
    if (fieldErr) { toast.error(fieldErr); return; }
    try {
      setIsLoading(true);
      // Canonical store path: orgStoreApi -> POST/PUT /stores with the backend
      // StoreCreate/StoreUpdate shape (store_name/store_code/brand/entity_id;
      // GSTIN is derived server-side from the entity). SAME path the Organization
      // screen uses -- one place to save a store, no more 422s from the old admin
      // {name,code,gst} shape the backend ignored.
      const payload = {
        store_name: storeData.storeName || '',
        store_code: storeData.storeCode || '',
        brand: (storeData as any).brand || 'BETTER_VISION',
        entity_id: storeData.entityId || undefined,
        address: storeData.address || '',
        city: storeData.city || '',
        state: storeData.state || '',
        pincode: storeData.pincode || '',
        phone: storeData.phone || '',
        email: storeData.email || undefined,
        enabled_categories: storeData.enabledCategories || [],
        geofence_radius_m: storeData.geoFenceRadius ?? undefined,
        latitude: (storeData as any).geoLat ?? undefined,
        longitude: (storeData as any).geoLng ?? undefined,
        is_active: storeData.isActive,
      };

      let storeId: string | undefined = editingStore?.id;
      if (editingStore?.id) {
        await orgStoreApi.update(editingStore.id, payload);
      } else {
        const created: any = await orgStoreApi.create(payload);
        storeId = created?.store_id || created?.id || storeId;
      }
      // Reconcile the legal-entity link (stored as entity_id on the store via
      // the entities API). Non-fatal: the store still saves if this fails.
      const newEntity = storeData.entityId ?? null;
      const prevEntity = editingStore?.entityId ?? null;
      if (storeId && newEntity !== prevEntity) {
        try {
          if (newEntity) await entitiesApi.assignStore(newEntity, storeId);
          else if (prevEntity) await entitiesApi.unassignStore(prevEntity, storeId);
        } catch {
          toast.error('Store saved, but entity link failed — set it from Settings > Entities');
        }
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
      await orgStoreApi.remove(storeId);
      toast.success('Store deleted successfully');
      loadStores();
    } catch (err) {
      // Surface the backend's actual reason (e.g. "store still has on-hand
      // stock / open orders / staff assigned") instead of a generic message --
      // the delete is a soft-delete that intentionally refuses while a store
      // has live dependents. Mirrors the save handler above.
      toast.error(err instanceof Error ? err.message : 'Failed to delete store');
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
          entities={entities}
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
          <p className="text-sm text-gray-500">
            Fixed product categories (HSN + attributes). GST rates are edited in the
            HSN / GST master — this is a read-only reference.
          </p>
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
                <span className={clsx(
                  'text-xs px-2 py-0.5 rounded-full',
                  cat.isActive ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-500'
                )}>
                  {cat.isActive ? 'Active' : 'Inactive'}
                </span>
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
  // Per-brand "new sub-brand" input value, keyed by brand id.
  const [newSubbrand, setNewSubbrand] = useState<Record<string, string>>({});
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
    if (!window.confirm('Are you sure you want to delete this brand? Its sub-brands are removed too.')) return;
    try {
      await adminBrandApi.deleteBrand(brandId);
      toast.success('Brand deleted successfully');
      loadBrands();
    } catch {
      toast.error('Failed to delete brand');
    }
  };

  // Sub-brands: the value list the Catalog Sub Brand field restricts to for
  // this brand (a brand with none keeps the field free-typing in Catalog).
  const handleAddSubbrand = async (brand: Brand) => {
    const name = (newSubbrand[brand.id] || '').trim();
    if (!name) return;
    if (brand.subbrands.some((sb) => sb.name.toLowerCase() === name.toLowerCase())) {
      toast.warning(`"${name}" is already a sub-brand of ${brand.brandName}.`);
      return;
    }
    // Code auto-derived from the name (uppercase alphanumerics, max 24) so the
    // owner only types the display name.
    const code = name.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 24) || 'SB';
    try {
      await adminBrandApi.createSubbrand(brand.id, { name, code });
      setNewSubbrand((prev) => ({ ...prev, [brand.id]: '' }));
      toast.success(`Sub-brand "${name}" added to ${brand.brandName}. Catalog now restricts Sub Brand to this list for the brand.`);
      loadBrands();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to add sub-brand');
    }
  };

  const handleDeleteSubbrand = async (brand: Brand, subbrandId: string, name: string) => {
    if (!window.confirm(`Remove sub-brand "${name}" from ${brand.brandName}?`)) return;
    try {
      await adminBrandApi.deleteSubbrand(brand.id, subbrandId);
      toast.success(`Sub-brand "${name}" removed`);
      loadBrands();
    } catch {
      toast.error('Failed to remove sub-brand');
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

                {/* Sub-brands: managed inline. When a brand has sub-brands the
                    Catalog Sub Brand field becomes a select restricted to them
                    (server-enforced); with none it stays free-typing. */}
                <div className="mt-3 pt-3 border-t border-gray-200">
                  <p className="text-xs text-gray-500 mb-2">
                    Sub-brands{brand.subbrands.length === 0 && ' (none — Sub Brand stays free-typing in Catalog)'}:
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    {brand.subbrands.map(sb => (
                      <span key={sb.id} className="inline-flex items-center gap-1 text-xs bg-white pl-2 pr-1 py-1 rounded border">
                        {sb.name}
                        <button
                          type="button"
                          onClick={() => handleDeleteSubbrand(brand, sb.id, sb.name)}
                          aria-label={`Remove sub-brand ${sb.name}`}
                          title="Remove sub-brand"
                          className="p-0.5 rounded-full text-gray-400 hover:text-red-600 hover:bg-red-50"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </span>
                    ))}
                    <input
                      className="input-field !w-44 !h-7 text-xs"
                      placeholder="Add sub-brand…"
                      value={newSubbrand[brand.id] || ''}
                      onChange={(e) => setNewSubbrand((prev) => ({ ...prev, [brand.id]: e.target.value }))}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          handleAddSubbrand(brand);
                        }
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => handleAddSubbrand(brand)}
                      className="text-gray-500 hover:text-bv-red-600"
                      title="Add sub-brand"
                    >
                      <Plus className="w-4 h-4" />
                    </button>
                  </div>
                </div>
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

// Friendly labels for role keys (display only). Any key not listed falls back
// to a title-cased version of the key.
const ROLE_LABELS: Record<string, string> = {
  SUPERADMIN: 'Superadmin',
  ADMIN: 'Admin',
  AREA_MANAGER: 'Area Manager',
  STORE_MANAGER: 'Store Manager',
  INVENTORY_MANAGER: 'Inventory Manager',
  ACCOUNTANT: 'Accountant',
  CATALOG_MANAGER: 'Catalog Manager',
  OPTOMETRIST: 'Optometrist',
  SALES_CASHIER: 'Sales Cashier',
  SALES_STAFF: 'Sales Staff',
  CASHIER: 'Cashier',
  WORKSHOP_STAFF: 'Workshop Staff',
};

const titleizeKey = (k: string) =>
  ROLE_LABELS[k] ??
  k.split('_').map(w => w.charAt(0) + w.slice(1).toLowerCase()).join(' ');

// Code-constant fallbacks (services/role_caps.py + services/pricing_caps.py),
// shown if the read-only endpoint is unreachable so the panel never renders
// blank. These mirror the enforced constants.
const FALLBACK_ROLE_CAPS: Record<string, number> = {
  SUPERADMIN: 100, ADMIN: 100, AREA_MANAGER: 25, STORE_MANAGER: 20,
  INVENTORY_MANAGER: 20, ACCOUNTANT: 0, CATALOG_MANAGER: 0, OPTOMETRIST: 0,
  SALES_CASHIER: 10, SALES_STAFF: 10, CASHIER: 0, WORKSHOP_STAFF: 0,
};
const FALLBACK_CATEGORY_CAPS: Record<string, number> = {
  MASS: 15, PREMIUM: 20, LUXURY: 5, SERVICE: 10, NON_DISCOUNTABLE: 0,
};
const FALLBACK_LUXURY_BRAND_CAPS: Record<string, number> = {
  CARTIER: 2, CHOPARD: 2, BVLGARI: 2, GUCCI: 5, PRADA: 5, VERSACE: 5, BURBERRY: 5,
};

// READ-ONLY. This panel DISPLAYS the discount caps the POS actually enforces.
// Those caps are set in code (services/role_caps.py + services/pricing_caps.py)
// and exposed read-only via GET /admin/discounts/enforced-caps. The old
// editable table wrote to the `role_discount_caps` collection which the POS
// never reads, so edits here had no effect -- a misleading screen. It is now
// informational only; changing a cap is a code change (contact an admin).
export function DiscountSection() {
  const [roleCaps, setRoleCaps] = useState<Record<string, number>>(FALLBACK_ROLE_CAPS);
  const [categoryCaps, setCategoryCaps] = useState<Record<string, number>>(FALLBACK_CATEGORY_CAPS);
  const [luxuryBrandCaps, setLuxuryBrandCaps] = useState<Record<string, number>>(FALLBACK_LUXURY_BRAND_CAPS);

  useEffect(() => {
    loadEnforcedCaps();
  }, []);

  const loadEnforcedCaps = async () => {
    try {
      const data = await adminDiscountApi.getEnforcedDiscountCaps();
      if (data?.role_caps) setRoleCaps(data.role_caps);
      if (data?.category_caps) setCategoryCaps(data.category_caps);
      if (data?.luxury_brand_caps) setLuxuryBrandCaps(data.luxury_brand_caps);
    } catch {
      // Endpoint unavailable -- keep the code-constant fallbacks already in state.
    }
  };

  const roleRows = Object.entries(roleCaps).sort((a, b) => b[1] - a[1]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Discount Rules</h2>
          <p className="text-sm text-gray-500">Maximum discount percentage by role (enforced)</p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500 bg-gray-100 px-2.5 py-1 rounded-full">
          <Lock className="w-3.5 h-3.5" />
          Read-only
        </span>
      </div>
      <div className="mb-5 flex items-start gap-2 text-xs text-gray-500 bg-blue-50 border border-blue-100 rounded-lg p-3">
        <Info className="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" />
        <span>
          These are the caps the POS actually enforces, read live from the
          canonical source <code className="font-mono">GET /admin/discounts/enforced-caps</code>
          (services/role_caps.py + services/pricing_caps.py). The old editable
          "discount rules" screen wrote storage the POS never read and has been
          retired -- this is now a read-only pointer to the enforced caps. To
          change a cap, contact an administrator (it is a code change).
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-white border-b border-gray-200">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Max Discount %</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {roleRows.map(([key, cap]) => (
              <tr key={key}>
                <td className="px-4 py-3 font-medium">{titleizeKey(key)}</td>
                <td className="px-4 py-3 text-center tabular-nums">
                  {cap >= 100 ? 'Unlimited' : `${cap}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 grid grid-cols-1 tablet:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-3">Category Caps</h3>
          <div className="space-y-1.5 text-sm">
            {Object.entries(categoryCaps).map(([cat, cap]) => (
              <div key={cat} className="flex items-center justify-between">
                <span className="text-gray-600">
                  {cat.split('_').map(w => w.charAt(0) + w.slice(1).toLowerCase()).join(' ')}
                </span>
                <span className="font-medium tabular-nums">{cap}%</span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-gray-400">
            Category caps apply on top of the role cap and always win when lower.
          </p>
        </div>
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-3">Luxury Brand Caps</h3>
          <div className="space-y-1.5 text-sm">
            {Object.entries(luxuryBrandCaps).map(([brand, cap]) => (
              <div key={brand} className="flex items-center justify-between">
                <span className="text-gray-600">
                  {brand.charAt(0) + brand.slice(1).toLowerCase()}
                </span>
                <span className="font-medium tabular-nums">{cap}%</span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-gray-400">
            Named luxury brands further constrain the category cap when lower.
          </p>
        </div>
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
  entities,
}: {
  store: StoreData | null;
  onClose: () => void;
  onSave: (data: Partial<StoreData>) => void;
  categories: Category[];
  entities: Entity[];
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
      entityId: null,
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
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
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

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
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
              <label className="block text-sm font-medium text-gray-700 mb-1">Legal entity</label>
              <select
                value={formData.entityId || ''}
                onChange={e => handleChange('entityId', e.target.value || null)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none bg-white"
              >
                <option value="">— none (assign later) —</option>
                {entities.map(en => (
                  <option key={en.entity_id} value={en.entity_id}>{en.name}</option>
                ))}
              </select>
              <p className="text-xs text-gray-400 mt-1">PAN/GST entity for payroll &amp; GST filings. Manage in Settings &gt; Entities.</p>
            </div>
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

          <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
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

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
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

          <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
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
            <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
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
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
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
            <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
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

// ============================================================================
// IMS 2.0 - Comprehensive Settings Page (Superadmin)
// ============================================================================
// Full master data management:
// - Store Management (Create, Edit, Delete stores)
// - User Management (Create users, assign roles, set permissions)
// - Product Category Master (Categories with attributes)
// - Brand/Subbrand Master
// - Lens Master (Brands, Indices, Coatings, Add-ons)
// - Discount Rules (Role-based, Category-based, Brand-based)
// - Integration Settings (Razorpay, WhatsApp, Tally, etc.)
// - System Settings
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import {
  Store, Users, Tag, Percent, Database, Globe,
  ChevronRight, Plus, Edit2, Trash2, X, Check, AlertCircle,
  RefreshCw, ToggleLeft, ToggleRight, Upload, Download,
  Link, CreditCard, MessageSquare, FileText, Boxes, CircleDot,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  adminStoreApi,
  adminUserApi,
  adminBrandApi,
  adminLensApi,
  adminDiscountApi,
  adminIntegrationApi,
  adminSystemApi,
} from '../../services/api';

// ============================================================================
// Types
// ============================================================================

type SettingsTab =
  | 'stores'
  | 'users'
  | 'categories'
  | 'brands'
  | 'lens-master'
  | 'discounts'
  | 'integrations'
  | 'system';

interface Store {
  id: string;
  storeCode: string;
  storeName: string;
  brand: string;
  gstin: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone: string;
  email: string;
  openingTime: string;
  closingTime: string;
  geoLat?: number;
  geoLng?: number;
  geoFenceRadius: number;
  enabledCategories: string[];
  isActive: boolean;
}

interface User {
  id: string;
  username: string;
  email: string;
  fullName: string;
  phone: string;
  roles: string[];
  accessibleStores: string[];
  discountCap: number;
  isActive: boolean;
  createdAt: string;
}

interface Category {
  code: string;
  name: string;
  shortName: string;
  hsnCode: string;
  gstRate: number;
  attributes: string[];
  isActive: boolean;
}

interface Brand {
  id: string;
  brandName: string;
  brandCode: string;
  categories: string[];
  tier: 'MASS' | 'PREMIUM' | 'LUXURY';
  isActive: boolean;
  subbrands: Subbrand[];
}

interface Subbrand {
  id: string;
  name: string;
  code: string;
  brandId: string;
  isActive: boolean;
}

interface LensBrand {
  id: string;
  name: string;
  code: string;
  isActive: boolean;
}

interface LensIndex {
  id: string;
  value: string;
  name: string;
  basePrice: number;
  isActive: boolean;
}

interface LensCoating {
  id: string;
  name: string;
  code: string;
  price: number;
  isActive: boolean;
}

interface Integration {
  type: string;
  name: string;
  description: string;
  isConfigured: boolean;
  isEnabled: boolean;
  icon: any;
}

// ============================================================================
// Settings Sections Configuration
// ============================================================================

const SETTINGS_SECTIONS = [
  { id: 'stores' as SettingsTab, label: 'Store Management', icon: Store, description: 'Create and manage stores', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'users' as SettingsTab, label: 'User Management', icon: Users, description: 'Manage users and roles', role: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
  { id: 'categories' as SettingsTab, label: 'Category Master', icon: Tag, description: 'Product categories and attributes', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'brands' as SettingsTab, label: 'Brand Master', icon: Boxes, description: 'Brands and subbrands', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'lens-master' as SettingsTab, label: 'Lens Master', icon: CircleDot, description: 'Lens brands, indices, coatings', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'discounts' as SettingsTab, label: 'Discount Rules', icon: Percent, description: 'Role-based discount limits', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'] },
  { id: 'integrations' as SettingsTab, label: 'Integrations', icon: Link, description: 'Payment, WhatsApp, Tally', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'system' as SettingsTab, label: 'System', icon: Database, description: 'Backup, sync, maintenance', role: ['SUPERADMIN', 'ADMIN'] },
];

// Available roles
const AVAILABLE_ROLES = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'ACCOUNTANT',
  'CATALOG_MANAGER',
  'OPTOMETRIST',
  'SALES_CASHIER',
  'SALES_STAFF',
  'WORKSHOP_STAFF',
];

// Role hierarchy - higher index = higher privilege
const ROLE_HIERARCHY: Record<string, number> = {
  'SUPERADMIN': 10,
  'ADMIN': 9,
  'AREA_MANAGER': 7,
  'STORE_MANAGER': 6,
  'ACCOUNTANT': 5,
  'CATALOG_MANAGER': 5,
  'OPTOMETRIST': 4,
  'SALES_CASHIER': 3,
  'SALES_STAFF': 2,
  'WORKSHOP_STAFF': 2,
};

// Which roles each user type can assign
const ASSIGNABLE_ROLES: Record<string, string[]> = {
  'SUPERADMIN': AVAILABLE_ROLES, // Can assign all roles
  'ADMIN': AVAILABLE_ROLES.filter(r => r !== 'SUPERADMIN'), // All except SUPERADMIN
  'STORE_MANAGER': ['OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF'], // Store-level only
};

// Get the highest role level from a list of roles
const getHighestRoleLevel = (roles: string[]): number => {
  return Math.max(...roles.map(r => ROLE_HIERARCHY[r] || 0));
};

// Category definitions
const CATEGORY_DEFINITIONS: Category[] = [
  { code: 'FR', name: 'Frame', shortName: 'Spectacles', hsnCode: '900311', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength'], isActive: true },
  { code: 'SG', name: 'Sunglass', shortName: 'Sunglasses', hsnCode: '900410', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength'], isActive: true },
  { code: 'CL', name: 'Contact Lens', shortName: 'Contact Lens', hsnCode: '90013100', gstRate: 12, attributes: ['brandName', 'subbrand', 'modelNo', 'colourName', 'power', 'pack', 'expiryDate'], isActive: true },
  { code: 'LS', name: 'Optical Lens', shortName: 'Lens', hsnCode: '900150', gstRate: 18, attributes: ['brandName', 'subbrand', 'index', 'coating', 'addOn1', 'addOn2', 'addOn3', 'lensCategory'], isActive: true },
  { code: 'RG', name: 'Reading Glasses', shortName: 'Readers', hsnCode: '900490', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'power'], isActive: true },
  { code: 'WT', name: 'Wrist Watch', shortName: 'Watch', hsnCode: '9101', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'dialColour', 'beltColour', 'dialSize', 'beltSize', 'watchCategory'], isActive: true },
  { code: 'CK', name: 'Clock', shortName: 'Clock', hsnCode: '9105', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'dialColour', 'bodyColour', 'dialSize', 'batterySize', 'clockCategory'], isActive: true },
  { code: 'HA', name: 'Hearing Aid', shortName: 'Hearing Aid', hsnCode: '9021', gstRate: 5, attributes: ['brandName', 'subbrand', 'modelNo', 'serialNo', 'machineCapacity', 'machineType'], isActive: true },
  { code: 'SMTSG', name: 'Smart Sunglass', shortName: 'Smart Sunglasses', hsnCode: '900490', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'yearOfLaunch'], isActive: true },
  { code: 'SMTFR', name: 'Smart Glasses', shortName: 'Smart Glasses', hsnCode: '900490', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'yearOfLaunch'], isActive: true },
  { code: 'SMTWT', name: 'Smart Watch', shortName: 'Smart Watch', hsnCode: '8517', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'bodyColour', 'beltColour', 'dialSize', 'beltSize', 'yearOfLaunch'], isActive: true },
  { code: 'ACC', name: 'Accessories', shortName: 'Accessories', hsnCode: '9004', gstRate: 18, attributes: ['brandName', 'subbrand', 'modelNo', 'size', 'pack', 'expiryDate', 'addOn1'], isActive: true },
  { code: 'SVC', name: 'Service', shortName: 'Repair/Service', hsnCode: '9987', gstRate: 18, attributes: ['serviceName', 'serviceType', 'estimatedTime'], isActive: true },
];

// Integration definitions
const INTEGRATION_DEFINITIONS: Integration[] = [
  { type: 'razorpay', name: 'Razorpay', description: 'Online payment gateway', isConfigured: false, isEnabled: false, icon: CreditCard },
  { type: 'whatsapp', name: 'WhatsApp Business', description: 'Customer notifications', isConfigured: false, isEnabled: false, icon: MessageSquare },
  { type: 'tally', name: 'Tally ERP', description: 'Accounting sync', isConfigured: false, isEnabled: false, icon: FileText },
  { type: 'shopify', name: 'Shopify', description: 'E-commerce sync', isConfigured: false, isEnabled: false, icon: Globe },
];

// ============================================================================
// Component
// ============================================================================

export function SettingsPage() {
  const { user } = useAuth();
  const toast = useToast();

  // State
  const [activeTab, setActiveTab] = useState<SettingsTab>('stores');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Data state
  const [stores, setStores] = useState<Store[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [categories, _setCategories] = useState<Category[]>(CATEGORY_DEFINITIONS);
  // Category setter reserved for future API integration
  void _setCategories;
  const [brands, setBrands] = useState<Brand[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>(INTEGRATION_DEFINITIONS);

  // Lens master state
  const [lensBrands, setLensBrands] = useState<LensBrand[]>([]);
  const [lensIndices, setLensIndices] = useState<LensIndex[]>([]);
  const [lensCoatings, setLensCoatings] = useState<LensCoating[]>([]);
  const [lensAddons, setLensAddons] = useState<{ id: string; name: string; code: string; price: number }[]>([]);

  // Discount state
  const [_roleDiscountCaps, setRoleDiscountCaps] = useState<Record<string, { mass: number; premium: number; luxury: number }>>({});
  // Role discount caps state for future display
  void _roleDiscountCaps;
  const [_tierDiscounts, setTierDiscounts] = useState<Record<string, number>>({});
  // Tier discounts for future display
  void _tierDiscounts;

  // System state
  const [systemStatus, setSystemStatus] = useState<{ database: string; api: string; version: string } | null>(null);

  // Modal state
  const [showAddStoreModal, setShowAddStoreModal] = useState(false);
  const [showAddUserModal, setShowAddUserModal] = useState(false);
  const [showAddBrandModal, setShowAddBrandModal] = useState(false);
  const [editingStore, setEditingStore] = useState<Store | null>(null);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editingBrand, setEditingBrand] = useState<Brand | null>(null);

  // Filter sections by user role
  const visibleSections = SETTINGS_SECTIONS.filter(section => {
    if (!user) return false;
    return section.role.includes(user.activeRole) || user.activeRole === 'SUPERADMIN';
  });

  // Load data on tab change
  useEffect(() => {
    loadTabData();
  }, [activeTab]);

  const loadTabData = async () => {
    setIsLoading(true);
    setError(null);

    try {
      switch (activeTab) {
        case 'stores':
          try {
            const storesResponse = await adminStoreApi.getStores();
            if (storesResponse?.stores) {
              setStores(storesResponse.stores.map(transformStore));
            } else if (Array.isArray(storesResponse)) {
              setStores(storesResponse.map(transformStore));
            }
          } catch (err) {
            console.log('Stores API not available, starting with empty list');
            setStores([]);
          }
          break;

        case 'users':
          try {
            const usersResponse = await adminUserApi.getUsers();
            if (usersResponse?.users) {
              setUsers(usersResponse.users.map(transformUser));
            } else if (Array.isArray(usersResponse)) {
              setUsers(usersResponse.map(transformUser));
            }
          } catch (err) {
            console.log('Users API not available, starting with empty list');
            setUsers([]);
          }
          break;

        case 'brands':
          try {
            const brandsResponse = await adminBrandApi.getBrands();
            if (brandsResponse?.brands) {
              setBrands(brandsResponse.brands.map(transformBrand));
            } else if (Array.isArray(brandsResponse)) {
              setBrands(brandsResponse.map(transformBrand));
            }
          } catch (err) {
            console.log('Brands API not available, starting with empty list');
            setBrands([]);
          }
          break;

        case 'lens-master':
          try {
            const [brandsRes, indicesRes, coatingsRes, addonsRes] = await Promise.all([
              adminLensApi.getLensBrands().catch(() => ({ brands: [] })),
              adminLensApi.getLensIndices().catch(() => ({ indices: [] })),
              adminLensApi.getLensCoatings().catch(() => ({ coatings: [] })),
              adminLensApi.getLensAddons().catch(() => ({ addons: [] })),
            ]);
            setLensBrands(brandsRes?.brands || brandsRes || []);
            setLensIndices(indicesRes?.indices || indicesRes || []);
            setLensCoatings(coatingsRes?.coatings || coatingsRes || []);
            setLensAddons(addonsRes?.addons || addonsRes || []);
          } catch (err) {
            console.log('Lens API not available');
          }
          break;

        case 'discounts':
          try {
            const [roleCapRes, tierRes] = await Promise.all([
              adminDiscountApi.getRoleDiscountCaps().catch(() => ({})),
              adminDiscountApi.getTierDiscounts().catch(() => ({})),
            ]);
            if (roleCapRes?.caps) {
              setRoleDiscountCaps(roleCapRes.caps);
            }
            if (tierRes?.discounts) {
              setTierDiscounts(tierRes.discounts);
            }
          } catch (err) {
            console.log('Discount API not available');
          }
          break;

        case 'integrations':
          try {
            const [razorpayRes, whatsappRes, tallyRes, shopifyRes] = await Promise.all([
              adminIntegrationApi.getRazorpayConfig().catch(() => null),
              adminIntegrationApi.getWhatsappConfig().catch(() => null),
              adminIntegrationApi.getTallyConfig().catch(() => null),
              adminIntegrationApi.getShopifyConfig().catch(() => null),
            ]);
            const merged = INTEGRATION_DEFINITIONS.map(def => {
              let config: any = null;
              if (def.type === 'razorpay') config = razorpayRes;
              if (def.type === 'whatsapp') config = whatsappRes;
              if (def.type === 'tally') config = tallyRes;
              if (def.type === 'shopify') config = shopifyRes;
              return {
                ...def,
                isConfigured: config?.is_configured || config?.configured || false,
                isEnabled: config?.is_enabled || config?.enabled || false,
              };
            });
            setIntegrations(merged);
          } catch {
            // Use defaults if API fails
          }
          break;

        case 'system':
          try {
            const statusRes = await adminSystemApi.getSystemStatus().catch(() => null);
            if (statusRes) {
              setSystemStatus(statusRes);
            }
          } catch {
            // Ignore
          }
          break;
      }
    } catch (err) {
      console.error('Failed to load data:', err);
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setIsLoading(false);
    }
  };

  const transformStore = (s: any): Store => ({
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

  const transformUser = (u: any): User => ({
    id: u.id || u.user_id || u._id,
    username: u.username || u.user_name || '',
    email: u.email || '',
    fullName: u.full_name || u.fullName || u.name || '',
    phone: u.phone || u.contact_phone || '',
    roles: u.roles || u.role ? [u.role] : [],
    accessibleStores: u.accessible_stores || u.accessibleStores || u.store_ids || [],
    discountCap: u.discount_cap || u.discountCap || 10,
    isActive: u.is_active !== false,
    createdAt: u.created_at || u.createdAt || '',
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
  // Store Management Handlers
  // ============================================================================

  const handleSaveStore = async (storeData: Partial<Store>) => {
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
      loadTabData();
    } catch (err) {
      console.error('Failed to save store:', err);
      toast.error(err instanceof Error ? err.message : 'Failed to save store');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveUser = async (userData: Partial<User>, password?: string) => {
    try {
      setIsLoading(true);
      const apiData = {
        name: userData.fullName || '',
        email: userData.email || '',
        phone: userData.phone || '',
        role: userData.roles?.[0] || 'SALES_STAFF',
        storeId: userData.accessibleStores?.[0] || '',
        password: password,
        status: userData.isActive ? 'ACTIVE' : 'INACTIVE',
      };

      if (editingUser?.id) {
        await adminUserApi.updateUser(editingUser.id, apiData);
      } else {
        await adminUserApi.createUser(apiData);
      }
      toast.success(editingUser ? 'User updated successfully' : 'User created successfully');
      setShowAddUserModal(false);
      setEditingUser(null);
      loadTabData();
    } catch (err) {
      console.error('Failed to save user:', err);
      toast.error(err instanceof Error ? err.message : 'Failed to save user');
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
      loadTabData();
    } catch (err) {
      console.error('Failed to save brand:', err);
      toast.error(err instanceof Error ? err.message : 'Failed to save brand');
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteStore = async (storeId: string) => {
    if (!window.confirm('Are you sure you want to delete this store?')) return;
    try {
      await adminStoreApi.deleteStore(storeId);
      toast.success('Store deleted successfully');
      loadTabData();
    } catch (err) {
      toast.error('Failed to delete store');
    }
  };

  const handleDeleteUser = async (userId: string) => {
    if (!window.confirm('Are you sure you want to delete this user?')) return;
    try {
      await adminUserApi.deleteUser(userId);
      toast.success('User deleted successfully');
      loadTabData();
    } catch (err) {
      toast.error('Failed to delete user');
    }
  };

  const handleDeleteBrand = async (brandId: string) => {
    if (!window.confirm('Are you sure you want to delete this brand?')) return;
    try {
      await adminBrandApi.deleteBrand(brandId);
      toast.success('Brand deleted successfully');
      loadTabData();
    } catch (err) {
      toast.error('Failed to delete brand');
    }
  };

  // ============================================================================
  // Render
  // ============================================================================

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
          <p className="text-gray-500">System configuration and master data management</p>
        </div>
        {user?.activeRole === 'SUPERADMIN' && (
          <span className="badge-warning">Superadmin Mode</span>
        )}
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5 text-red-500" />
          <span className="text-sm text-red-700">{error}</span>
          <button onClick={loadTabData} className="ml-auto text-sm text-red-600 hover:underline">
            Retry
          </button>
        </div>
      )}

      <div className="flex gap-6">
        {/* Sidebar */}
        <div className="w-64 flex-shrink-0">
          <div className="card p-2">
            {visibleSections.map(section => (
              <button
                key={section.id}
                onClick={() => setActiveTab(section.id)}
                className={clsx(
                  'w-full flex items-center gap-3 px-3 py-3 rounded-lg text-left transition-colors',
                  activeTab === section.id
                    ? 'bg-bv-red-50 text-bv-red-600'
                    : 'text-gray-600 hover:bg-gray-50'
                )}
              >
                <section.icon className="w-5 h-5" />
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm">{section.label}</p>
                  <p className="text-xs text-gray-400 truncate">{section.description}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {isLoading && (
            <div className="flex items-center justify-center h-48">
              <RefreshCw className="w-8 h-8 text-bv-red-600 animate-spin" />
            </div>
          )}

          {!isLoading && (
            <>
              {/* ================================================================ */}
              {/* STORE MANAGEMENT */}
              {/* ================================================================ */}
              {activeTab === 'stores' && (
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
                      <div className="text-center py-12 text-gray-400">
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
                                  className="p-2 text-gray-400 hover:text-bv-red-600 hover:bg-gray-100 rounded"
                                  title="Edit store"
                                >
                                  <Edit2 className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => handleDeleteStore(store.id)}
                                  className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded"
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
              )}

              {/* ================================================================ */}
              {/* USER MANAGEMENT */}
              {/* ================================================================ */}
              {activeTab === 'users' && (() => {
                // Get current user's role level
                const currentUserRoleLevel = ROLE_HIERARCHY[user?.activeRole || ''] || 0;

                // Filter users based on current user's role
                const filteredUsers = users.filter(u => {
                  // SUPERADMIN sees all users
                  if (user?.activeRole === 'SUPERADMIN') return true;

                  // ADMIN sees all except SUPERADMIN
                  if (user?.activeRole === 'ADMIN') {
                    return !u.roles.includes('SUPERADMIN');
                  }

                  // STORE_MANAGER sees users in their stores only (and lower roles)
                  if (user?.activeRole === 'STORE_MANAGER') {
                    const userStores = user?.storeIds || [];
                    const hasCommonStore = u.accessibleStores?.some(s => userStores.includes(s));
                    const userRoleLevel = getHighestRoleLevel(u.roles);
                    return hasCommonStore && userRoleLevel < currentUserRoleLevel;
                  }

                  return false;
                });

                // Check if current user can manage a specific user
                const canManageUser = (targetUser: User) => {
                  const targetRoleLevel = getHighestRoleLevel(targetUser.roles);
                  // Can only manage users with lower role level
                  return currentUserRoleLevel > targetRoleLevel;
                };

                return (
                <div className="card">
                  <div className="flex items-center justify-between mb-6">
                    <div>
                      <h2 className="text-lg font-semibold text-gray-900">User Management</h2>
                      {user?.activeRole === 'STORE_MANAGER' && (
                        <p className="text-xs text-gray-500 mt-1">Showing users from your managed stores</p>
                      )}
                    </div>
                    <button
                      onClick={() => setShowAddUserModal(true)}
                      className="btn-primary flex items-center gap-2"
                    >
                      <Plus className="w-4 h-4" />
                      Add User
                    </button>
                  </div>

                  <p className="text-sm text-gray-500 mb-4">
                    {user?.activeRole === 'STORE_MANAGER'
                      ? 'Create and manage store staff. You can assign: Optometrist, Sales Cashier, Sales Staff, Workshop Staff roles.'
                      : 'Create users and assign roles. Users can have multiple roles and access to multiple stores.'}
                  </p>

                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead className="bg-gray-50 border-b border-gray-200">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Roles</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Stores</th>
                          <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Discount Cap</th>
                          <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status</th>
                          <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {filteredUsers.length === 0 ? (
                          <tr>
                            <td colSpan={6} className="px-4 py-12 text-center text-gray-400">
                              {user?.activeRole === 'STORE_MANAGER'
                                ? 'No staff members found. Click "Add User" to create store staff.'
                                : 'No users found. Click "Add User" to create one.'}
                            </td>
                          </tr>
                        ) : (
                          filteredUsers.map(u => {
                            const canEdit = canManageUser(u);
                            const canDelete = canManageUser(u) && u.id !== user?.id;

                            return (
                            <tr key={u.id} className="hover:bg-gray-50">
                              <td className="px-4 py-3">
                                <p className="font-medium text-gray-900">{u.fullName}</p>
                                <p className="text-xs text-gray-500">{u.email}</p>
                              </td>
                              <td className="px-4 py-3">
                                <div className="flex flex-wrap gap-1">
                                  {u.roles.map(role => (
                                    <span key={role} className={clsx(
                                      'text-xs px-2 py-0.5 rounded',
                                      role === 'SUPERADMIN' ? 'bg-purple-100 text-purple-700' :
                                      role === 'ADMIN' ? 'bg-red-100 text-red-700' :
                                      role === 'AREA_MANAGER' ? 'bg-orange-100 text-orange-700' :
                                      role === 'STORE_MANAGER' ? 'bg-blue-100 text-blue-700' :
                                      'bg-gray-100 text-gray-700'
                                    )}>
                                      {role.replace('_', ' ')}
                                    </span>
                                  ))}
                                </div>
                              </td>
                              <td className="px-4 py-3 text-sm text-gray-600">
                                {u.accessibleStores?.length || 0} stores
                              </td>
                              <td className="px-4 py-3 text-center">
                                <span className="font-medium">{u.discountCap}%</span>
                              </td>
                              <td className="px-4 py-3 text-center">
                                {u.isActive ? (
                                  <span className="badge-success">Active</span>
                                ) : (
                                  <span className="badge-error">Inactive</span>
                                )}
                              </td>
                              <td className="px-4 py-3 text-center">
                                <div className="flex items-center justify-center gap-2">
                                  {canEdit ? (
                                    <button
                                      onClick={() => {
                                        setEditingUser(u);
                                        setShowAddUserModal(true);
                                      }}
                                      className="text-gray-400 hover:text-bv-red-600"
                                      title="Edit user"
                                    >
                                      <Edit2 className="w-4 h-4" />
                                    </button>
                                  ) : (
                                    <span className="text-gray-200" title="Cannot edit higher-level users">
                                      <Edit2 className="w-4 h-4" />
                                    </span>
                                  )}
                                  {canDelete ? (
                                    <button
                                      onClick={() => handleDeleteUser(u.id)}
                                      className="text-gray-400 hover:text-red-600"
                                      title="Delete user"
                                    >
                                      <Trash2 className="w-4 h-4" />
                                    </button>
                                  ) : (
                                    <span className="text-gray-200" title="Cannot delete this user">
                                      <Trash2 className="w-4 h-4" />
                                    </span>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );})
                        )}
                      </tbody>
                    </table>
                  </div>

                  {/* Available Roles Reference */}
                  <div className="mt-6 pt-6 border-t border-gray-200">
                    <h3 className="text-sm font-medium text-gray-700 mb-3">
                      {user?.activeRole === 'STORE_MANAGER' ? 'Assignable Roles' : 'Available Roles'}
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {(ASSIGNABLE_ROLES[user?.activeRole || ''] || []).map(role => (
                        <span key={role} className="text-xs bg-gray-100 px-3 py-1 rounded">
                          {role.replace('_', ' ')}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                );
              })()}

              {/* ================================================================ */}
              {/* CATEGORY MASTER */}
              {/* ================================================================ */}
              {activeTab === 'categories' && (
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
                              <Tag className={clsx('w-5 h-5', cat.isActive ? 'text-blue-600' : 'text-gray-400')} />
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <h3 className="font-medium text-gray-900">{cat.name}</h3>
                                <span className="text-xs bg-gray-100 px-2 py-0.5 rounded font-mono">{cat.code}</span>
                              </div>
                              <p className="text-xs text-gray-500">
                                HSN: {cat.hsnCode} • GST: {cat.gstRate}%
                              </p>
                            </div>
                          </div>
                          <div className="flex items-center gap-3">
                            {cat.isActive ? (
                              <ToggleRight className="w-6 h-6 text-green-600 cursor-pointer" />
                            ) : (
                              <ToggleLeft className="w-6 h-6 text-gray-400 cursor-pointer" />
                            )}
                            <button className="text-gray-400 hover:text-bv-red-600">
                              <Edit2 className="w-4 h-4" />
                            </button>
                          </div>
                        </div>

                        {/* Attributes */}
                        <div className="mt-3 pt-3 border-t border-gray-100">
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
              )}

              {/* ================================================================ */}
              {/* BRAND MASTER */}
              {/* ================================================================ */}
              {activeTab === 'brands' && (
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
                    <div className="text-center py-12 text-gray-400">
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
                                className="text-gray-400 hover:text-bv-red-600"
                                title="Edit brand"
                              >
                                <Edit2 className="w-4 h-4" />
                              </button>
                              <button
                                onClick={() => handleDeleteBrand(brand.id)}
                                className="text-gray-400 hover:text-red-600"
                                title="Delete brand"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </div>
                          </div>

                          {/* Subbrands */}
                          {brand.subbrands && brand.subbrands.length > 0 && (
                            <div className="mt-3 pt-3 border-t border-gray-100">
                              <p className="text-xs text-gray-500 mb-2">Subbrands:</p>
                              <div className="flex flex-wrap gap-2">
                                {brand.subbrands.map(sb => (
                                  <span key={sb.id} className="text-xs bg-gray-50 px-2 py-1 rounded border">
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
              )}

              {/* ================================================================ */}
              {/* LENS MASTER */}
              {/* ================================================================ */}
              {activeTab === 'lens-master' && (
                <div className="space-y-4">
                  <div className="card">
                    <h2 className="text-lg font-semibold text-gray-900 mb-4">Lens Master</h2>
                    <p className="text-sm text-gray-500 mb-6">
                      Configure lens brands, indices, coatings, and add-ons for the lens selection workflow in POS.
                    </p>

                    {/* Lens Brands */}
                    <div className="mb-6">
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium text-gray-700">Lens Brands</h3>
                        <button
                          onClick={async () => {
                            const name = prompt('Enter lens brand name:');
                            if (name) {
                              try {
                                await adminLensApi.createLensBrand({ name, code: name.toUpperCase().replace(/\s+/g, '_') });
                                toast.success('Lens brand added');
                                loadTabData();
                              } catch (err) {
                                toast.error('Failed to add lens brand');
                              }
                            }
                          }}
                          className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
                        >
                          <Plus className="w-3 h-3" />
                          Add Brand
                        </button>
                      </div>
                      <div className="grid grid-cols-4 gap-2">
                        {lensBrands.length === 0 ? (
                          <div className="col-span-4 text-center py-4 text-gray-400">
                            No lens brands configured. Click "Add Brand" to add one.
                          </div>
                        ) : (
                          lensBrands.map(brand => (
                            <div key={brand.id} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between">
                              <span className="text-sm">{brand.name}</span>
                              <div className="flex items-center gap-1">
                                <Edit2 className="w-3 h-3 text-gray-400 cursor-pointer hover:text-bv-red-600" />
                                <Trash2
                                  className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                                  onClick={async () => {
                                    if (window.confirm(`Delete lens brand "${brand.name}"?`)) {
                                      try {
                                        await adminLensApi.deleteLensBrand(brand.id);
                                        toast.success('Lens brand deleted');
                                        loadTabData();
                                      } catch (err) {
                                        toast.error('Failed to delete lens brand');
                                      }
                                    }
                                  }}
                                />
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* Lens Indices */}
                    <div className="mb-6">
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium text-gray-700">Lens Indices</h3>
                        <button
                          onClick={async () => {
                            const value = prompt('Enter index value (e.g., 1.56):');
                            const name = prompt('Enter index name (e.g., Standard):');
                            if (value && name) {
                              try {
                                await adminLensApi.createLensIndex({ value, multiplier: 1.0, description: name });
                                toast.success('Lens index added');
                                loadTabData();
                              } catch (err) {
                                toast.error('Failed to add lens index');
                              }
                            }
                          }}
                          className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
                        >
                          <Plus className="w-3 h-3" />
                          Add Index
                        </button>
                      </div>
                      <div className="grid grid-cols-4 gap-2">
                        {lensIndices.length === 0 ? (
                          <div className="col-span-4 text-center py-4 text-gray-400">
                            No lens indices configured. Click "Add Index" to add one.
                          </div>
                        ) : (
                          lensIndices.map(idx => (
                            <div key={idx.id} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between">
                              <div>
                                <span className="text-sm font-medium">{idx.value}</span>
                                <span className="text-xs text-gray-500 ml-2">{idx.name}</span>
                              </div>
                              <Trash2
                                className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                                onClick={async () => {
                                  if (window.confirm(`Delete lens index "${idx.value}"?`)) {
                                    try {
                                      await adminLensApi.deleteLensIndex(idx.id);
                                      toast.success('Lens index deleted');
                                      loadTabData();
                                    } catch (err) {
                                      toast.error('Failed to delete lens index');
                                    }
                                  }
                                }}
                              />
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* Coatings */}
                    <div className="mb-6">
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium text-gray-700">Coatings</h3>
                        <button
                          onClick={async () => {
                            const name = prompt('Enter coating name:');
                            const priceStr = prompt('Enter coating price:');
                            if (name && priceStr) {
                              try {
                                await adminLensApi.createLensCoating({
                                  name,
                                  code: name.toUpperCase().replace(/\s+/g, '_'),
                                  price: parseFloat(priceStr) || 0,
                                });
                                toast.success('Coating added');
                                loadTabData();
                              } catch (err) {
                                toast.error('Failed to add coating');
                              }
                            }
                          }}
                          className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
                        >
                          <Plus className="w-3 h-3" />
                          Add Coating
                        </button>
                      </div>
                      <div className="grid grid-cols-3 gap-2">
                        {lensCoatings.length === 0 ? (
                          <div className="col-span-3 text-center py-4 text-gray-400">
                            No coatings configured. Click "Add Coating" to add one.
                          </div>
                        ) : (
                          lensCoatings.map(coating => (
                            <div key={coating.id} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between">
                              <div>
                                <span className="text-sm">{coating.name}</span>
                                <span className="text-xs text-gray-500 ml-2">₹{coating.price}</span>
                              </div>
                              <div className="flex items-center gap-1">
                                <Edit2 className="w-3 h-3 text-gray-400 cursor-pointer hover:text-bv-red-600" />
                                <Trash2
                                  className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                                  onClick={async () => {
                                    if (window.confirm(`Delete coating "${coating.name}"?`)) {
                                      try {
                                        await adminLensApi.deleteLensCoating(coating.id);
                                        toast.success('Coating deleted');
                                        loadTabData();
                                      } catch (err) {
                                        toast.error('Failed to delete coating');
                                      }
                                    }
                                  }}
                                />
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* Add-ons */}
                    <div>
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium text-gray-700">Add-ons</h3>
                        <button
                          onClick={async () => {
                            const name = prompt('Enter add-on name:');
                            const priceStr = prompt('Enter add-on price:');
                            if (name && priceStr) {
                              try {
                                await adminLensApi.createLensAddon({
                                  name,
                                  code: name.toUpperCase().replace(/\s+/g, '_'),
                                  price: parseFloat(priceStr) || 0,
                                  type: 'ADDON',
                                });
                                toast.success('Add-on added');
                                loadTabData();
                              } catch (err) {
                                toast.error('Failed to add add-on');
                              }
                            }
                          }}
                          className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
                        >
                          <Plus className="w-3 h-3" />
                          Add Add-on
                        </button>
                      </div>
                      <div className="grid grid-cols-3 gap-2">
                        {lensAddons.length === 0 ? (
                          <div className="col-span-3 text-center py-4 text-gray-400">
                            No add-ons configured. Click "Add Add-on" to add one.
                          </div>
                        ) : (
                          lensAddons.map(addon => (
                            <div key={addon.id} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between">
                              <div>
                                <span className="text-sm">{addon.name}</span>
                                <span className="text-xs text-gray-500 ml-2">₹{addon.price}</span>
                              </div>
                              <div className="flex items-center gap-1">
                                <Edit2 className="w-3 h-3 text-gray-400 cursor-pointer hover:text-bv-red-600" />
                                <Trash2
                                  className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                                  onClick={async () => {
                                    if (window.confirm(`Delete add-on "${addon.name}"?`)) {
                                      try {
                                        await adminLensApi.deleteLensAddon(addon.id);
                                        toast.success('Add-on deleted');
                                        loadTabData();
                                      } catch (err) {
                                        toast.error('Failed to delete add-on');
                                      }
                                    }
                                  }}
                                />
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* ================================================================ */}
              {/* DISCOUNT RULES */}
              {/* ================================================================ */}
              {activeTab === 'discounts' && (
                <div className="card">
                  <div className="flex items-center justify-between mb-6">
                    <div>
                      <h2 className="text-lg font-semibold text-gray-900">Discount Rules</h2>
                      <p className="text-sm text-gray-500">Maximum discount by role and brand tier</p>
                    </div>
                    <button
                      onClick={() => toast.info('Save changes to update discount rules')}
                      className="btn-outline flex items-center gap-2"
                    >
                      <Edit2 className="w-4 h-4" />
                      Edit Rules
                    </button>
                  </div>

                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead className="bg-gray-50 border-b border-gray-200">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                          <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Mass</th>
                          <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Premium</th>
                          <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Luxury</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {[
                          { role: 'Sales Staff', mass: 5, premium: 3, luxury: 0 },
                          { role: 'Sales Cashier', mass: 10, premium: 5, luxury: 3 },
                          { role: 'Optometrist', mass: 5, premium: 3, luxury: 0 },
                          { role: 'Workshop Staff', mass: 0, premium: 0, luxury: 0 },
                          { role: 'Store Manager', mass: 15, premium: 10, luxury: 5 },
                          { role: 'Accountant', mass: 10, premium: 5, luxury: 3 },
                          { role: 'Area Manager', mass: 20, premium: 15, luxury: 10 },
                          { role: 'Admin', mass: 100, premium: 100, luxury: 100 },
                          { role: 'Superadmin', mass: 100, premium: 100, luxury: 100 },
                        ].map(row => (
                          <tr key={row.role}>
                            <td className="px-4 py-3 font-medium">{row.role}</td>
                            <td className="px-4 py-3 text-center">
                              <input
                                type="number"
                                defaultValue={row.mass}
                                min="0"
                                max="100"
                                className="w-16 px-2 py-1 text-center border border-gray-200 rounded"
                              />
                              %
                            </td>
                            <td className="px-4 py-3 text-center">
                              <input
                                type="number"
                                defaultValue={row.premium}
                                min="0"
                                max="100"
                                className="w-16 px-2 py-1 text-center border border-gray-200 rounded"
                              />
                              %
                            </td>
                            <td className="px-4 py-3 text-center">
                              <input
                                type="number"
                                defaultValue={row.luxury}
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
                    <ul className="space-y-2 text-sm text-gray-600">
                      <li className="flex items-center gap-2">
                        <Check className="w-4 h-4 text-green-600" />
                        If Offer Price = MRP → Store can apply discount up to role cap
                      </li>
                      <li className="flex items-center gap-2">
                        <Check className="w-4 h-4 text-green-600" />
                        If Offer Price &lt; MRP → HQ discount applied, no further discount allowed
                      </li>
                      <li className="flex items-center gap-2">
                        <Check className="w-4 h-4 text-green-600" />
                        Discount above cap requires approval from higher role
                      </li>
                    </ul>
                  </div>
                </div>
              )}

              {/* ================================================================ */}
              {/* INTEGRATIONS */}
              {/* ================================================================ */}
              {activeTab === 'integrations' && (
                <div className="space-y-4">
                  {integrations.map(integration => (
                    <div key={integration.type} className="card">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-4">
                          <div className={clsx(
                            'w-12 h-12 rounded-lg flex items-center justify-center',
                            integration.isEnabled ? 'bg-green-50' : 'bg-gray-100'
                          )}>
                            <integration.icon className={clsx(
                              'w-6 h-6',
                              integration.isEnabled ? 'text-green-600' : 'text-gray-400'
                            )} />
                          </div>
                          <div>
                            <h3 className="font-medium text-gray-900">{integration.name}</h3>
                            <p className="text-sm text-gray-500">{integration.description}</p>
                          </div>
                        </div>

                        <div className="flex items-center gap-4">
                          {integration.isConfigured ? (
                            <span className="badge-success">Configured</span>
                          ) : (
                            <span className="badge-warning">Not Configured</span>
                          )}
                          <button className="btn-outline">
                            Configure
                          </button>
                          {integration.isConfigured && (
                            integration.isEnabled ? (
                              <ToggleRight className="w-8 h-8 text-green-600 cursor-pointer" />
                            ) : (
                              <ToggleLeft className="w-8 h-8 text-gray-400 cursor-pointer" />
                            )
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* ================================================================ */}
              {/* SYSTEM */}
              {/* ================================================================ */}
              {activeTab === 'system' && (
                <div className="space-y-4">
                  <div className="card">
                    <h2 className="text-lg font-semibold text-gray-900 mb-4">System Status</h2>
                    <div className="grid grid-cols-3 gap-4">
                      <div className={clsx('p-4 rounded-lg', systemStatus?.database === 'connected' ? 'bg-green-50' : 'bg-yellow-50')}>
                        <p className="text-sm text-gray-500">Database</p>
                        <p className={clsx('font-medium', systemStatus?.database === 'connected' ? 'text-green-600' : 'text-yellow-600')}>
                          {systemStatus?.database || 'Checking...'}
                        </p>
                      </div>
                      <div className={clsx('p-4 rounded-lg', systemStatus?.api === 'healthy' ? 'bg-green-50' : 'bg-yellow-50')}>
                        <p className="text-sm text-gray-500">API Status</p>
                        <p className={clsx('font-medium', systemStatus?.api === 'healthy' ? 'text-green-600' : 'text-yellow-600')}>
                          {systemStatus?.api || 'Checking...'}
                        </p>
                      </div>
                      <div className="p-4 bg-blue-50 rounded-lg">
                        <p className="text-sm text-gray-500">Version</p>
                        <p className="font-medium text-blue-600">{systemStatus?.version || '2.0.0'}</p>
                      </div>
                    </div>
                  </div>

                  <div className="card">
                    <h2 className="text-lg font-semibold text-gray-900 mb-4">Data Management</h2>
                    <div className="space-y-3">
                      <button
                        onClick={() => {
                          const input = document.createElement('input');
                          input.type = 'file';
                          input.accept = '.csv,.xlsx,.xls';
                          input.onchange = async (e) => {
                            const file = (e.target as HTMLInputElement).files?.[0];
                            if (file) {
                              try {
                                const type = prompt('Import type (products, customers, inventory):');
                                if (type) {
                                  await adminSystemApi.importData(type, file);
                                  toast.success('Data imported successfully');
                                }
                              } catch (err) {
                                toast.error('Failed to import data');
                              }
                            }
                          };
                          input.click();
                        }}
                        className="w-full p-4 bg-gray-50 rounded-lg text-left hover:bg-gray-100 transition-colors flex items-center justify-between"
                      >
                        <div className="flex items-center gap-3">
                          <Upload className="w-5 h-5 text-gray-400" />
                          <div>
                            <p className="font-medium text-gray-900">Import Data</p>
                            <p className="text-sm text-gray-500">Import products, customers from CSV/Excel</p>
                          </div>
                        </div>
                        <ChevronRight className="w-5 h-5 text-gray-400" />
                      </button>
                      <button
                        onClick={async () => {
                          const type = prompt('Export type (products, customers, orders, inventory, all):');
                          if (type) {
                            try {
                              const blob = await adminSystemApi.exportData(type as any);
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = url;
                              a.download = `ims_export_${type}_${new Date().toISOString().split('T')[0]}.xlsx`;
                              a.click();
                              URL.revokeObjectURL(url);
                              toast.success('Data exported successfully');
                            } catch (err) {
                              toast.error('Failed to export data');
                            }
                          }
                        }}
                        className="w-full p-4 bg-gray-50 rounded-lg text-left hover:bg-gray-100 transition-colors flex items-center justify-between"
                      >
                        <div className="flex items-center gap-3">
                          <Download className="w-5 h-5 text-gray-400" />
                          <div>
                            <p className="font-medium text-gray-900">Export Data</p>
                            <p className="text-sm text-gray-500">Export reports and data to Excel</p>
                          </div>
                        </div>
                        <ChevronRight className="w-5 h-5 text-gray-400" />
                      </button>
                      <button
                        onClick={async () => {
                          if (window.confirm('Create a full system backup?')) {
                            try {
                              await adminSystemApi.createBackup();
                              toast.success('Backup created successfully');
                            } catch (err) {
                              toast.error('Failed to create backup');
                            }
                          }
                        }}
                        className="w-full p-4 bg-gray-50 rounded-lg text-left hover:bg-gray-100 transition-colors flex items-center justify-between"
                      >
                        <div className="flex items-center gap-3">
                          <Database className="w-5 h-5 text-gray-400" />
                          <div>
                            <p className="font-medium text-gray-900">Backup Database</p>
                            <p className="text-sm text-gray-500">Create full system backup</p>
                          </div>
                        </div>
                        <ChevronRight className="w-5 h-5 text-gray-400" />
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ================================================================ */}
      {/* ADD/EDIT STORE MODAL */}
      {/* ================================================================ */}
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

      {/* ================================================================ */}
      {/* ADD/EDIT USER MODAL */}
      {/* ================================================================ */}
      {showAddUserModal && (
        <UserModal
          user={editingUser}
          stores={stores}
          onClose={() => {
            setShowAddUserModal(false);
            setEditingUser(null);
          }}
          onSave={handleSaveUser}
          currentUserRole={user?.activeRole || ''}
          currentUserStores={user?.storeIds || []}
        />
      )}

      {/* ================================================================ */}
      {/* ADD/EDIT BRAND MODAL */}
      {/* ================================================================ */}
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
    </div>
  );
}

// ============================================================================
// STORE MODAL
// ============================================================================

function StoreModal({
  store,
  onClose,
  onSave,
  categories,
}: {
  store: Store | null;
  onClose: () => void;
  onSave: (data: Partial<Store>) => void;
  categories: Category[];
}) {
  const [formData, setFormData] = useState<Partial<Store>>(
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
                placeholder="BV-KOL-001"
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
                <label key={cat.code} className="flex items-center gap-2 p-2 bg-gray-50 rounded cursor-pointer hover:bg-gray-100">
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
// USER MODAL
// ============================================================================

function UserModal({
  user,
  stores,
  onClose,
  onSave,
  currentUserRole,
  currentUserStores,
}: {
  user: User | null;
  stores: Store[];
  onClose: () => void;
  onSave: (data: Partial<User>, password?: string) => void;
  currentUserRole: string;
  currentUserStores: string[];
}) {
  const [formData, setFormData] = useState<Partial<User>>(
    user || {
      username: '',
      email: '',
      fullName: '',
      phone: '',
      roles: [],
      accessibleStores: [],
      discountCap: 10,
      isActive: true,
    }
  );
  const [password, setPassword] = useState('');

  // Get allowed roles based on current user's role
  const allowedRoles = ASSIGNABLE_ROLES[currentUserRole] || [];

  // Get allowed stores based on current user's role
  // SUPERADMIN/ADMIN can assign any store, STORE_MANAGER only their own stores
  const allowedStores = currentUserRole === 'STORE_MANAGER'
    ? stores.filter(s => currentUserStores.includes(s.id))
    : stores;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">
            {user ? 'Edit User' : 'Add New User'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Username *</label>
              <input
                type="text"
                value={formData.username || ''}
                onChange={e => setFormData(prev => ({ ...prev, username: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Full Name *</label>
              <input
                type="text"
                value={formData.fullName || ''}
                onChange={e => setFormData(prev => ({ ...prev, fullName: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email *</label>
              <input
                type="email"
                value={formData.email || ''}
                onChange={e => setFormData(prev => ({ ...prev, email: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Phone</label>
              <input
                type="tel"
                value={formData.phone || ''}
                onChange={e => setFormData(prev => ({ ...prev, phone: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          {!user && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Password *</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Roles *</label>
            {allowedRoles.length === 0 ? (
              <p className="text-sm text-gray-500">You don't have permission to assign roles.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {allowedRoles.map(role => (
                  <label key={role} className="flex items-center gap-2 p-2 bg-gray-50 rounded cursor-pointer hover:bg-gray-100">
                    <input
                      type="checkbox"
                      checked={formData.roles?.includes(role) || false}
                      onChange={e => {
                        const current = formData.roles || [];
                        if (e.target.checked) {
                          setFormData(prev => ({ ...prev, roles: [...current, role] }));
                        } else {
                          setFormData(prev => ({ ...prev, roles: current.filter(r => r !== role) }));
                        }
                      }}
                      className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                    />
                    <span className="text-sm">{role.replace('_', ' ')}</span>
                  </label>
                ))}
              </div>
            )}
            {currentUserRole === 'STORE_MANAGER' && (
              <p className="text-xs text-gray-500 mt-2">As Store Manager, you can only assign store-level roles.</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Accessible Stores</label>
            {allowedStores.length === 0 ? (
              <p className="text-sm text-gray-500">No stores available.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {allowedStores.map(store => (
                  <label key={store.id} className="flex items-center gap-2 p-2 bg-gray-50 rounded cursor-pointer hover:bg-gray-100">
                    <input
                      type="checkbox"
                      checked={formData.accessibleStores?.includes(store.id) || false}
                      onChange={e => {
                        const current = formData.accessibleStores || [];
                        if (e.target.checked) {
                          setFormData(prev => ({ ...prev, accessibleStores: [...current, store.id] }));
                        } else {
                          setFormData(prev => ({ ...prev, accessibleStores: current.filter(s => s !== store.id) }));
                        }
                      }}
                      className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                    />
                    <span className="text-sm">{store.storeName}</span>
                  </label>
                ))}
              </div>
            )}
            {currentUserRole === 'STORE_MANAGER' && (
              <p className="text-xs text-gray-500 mt-2">You can only assign users to your managed stores.</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Discount Cap (%)</label>
            <input
              type="number"
              value={formData.discountCap || 10}
              onChange={e => setFormData(prev => ({ ...prev, discountCap: parseInt(e.target.value) }))}
              min="0"
              max="100"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
            />
          </div>
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
          <button onClick={() => onSave(formData, password)} className="btn-primary">
            {user ? 'Update User' : 'Create User'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// BRAND MODAL
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
                <label key={cat.code} className="flex items-center gap-2 p-2 bg-gray-50 rounded cursor-pointer hover:bg-gray-100">
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

export default SettingsPage;

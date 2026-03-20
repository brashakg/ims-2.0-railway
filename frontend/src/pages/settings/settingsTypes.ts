// ============================================================================
// IMS 2.0 - Settings Shared Types & Constants
// ============================================================================

export type SettingsTab =
  | 'profile'
  | 'business'
  | 'stores'
  | 'users'
  | 'categories'
  | 'brands'
  | 'lens-master'
  | 'discounts'
  | 'tax-invoice'
  | 'notifications'
  | 'integrations'
  | 'printers'
  | 'audit-logs'
  | 'approvals'
  | 'feature-toggles'
  | 'system';

export interface StoreData {
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

export interface UserData {
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

export interface Category {
  code: string;
  name: string;
  shortName: string;
  hsnCode: string;
  gstRate: number;
  attributes: string[];
  isActive: boolean;
}

export interface Brand {
  id: string;
  brandName: string;
  brandCode: string;
  categories: string[];
  tier: 'MASS' | 'PREMIUM' | 'LUXURY';
  isActive: boolean;
  subbrands: Subbrand[];
}

export interface Subbrand {
  id: string;
  name: string;
  code: string;
  brandId: string;
  isActive: boolean;
}

export interface LensBrand {
  id: string;
  name: string;
  code: string;
  isActive: boolean;
}

export interface LensIndex {
  id: string;
  value: string;
  name: string;
  basePrice: number;
  isActive: boolean;
}

export interface LensCoating {
  id: string;
  name: string;
  code: string;
  price: number;
  isActive: boolean;
}

export interface LensAddon {
  id: string;
  name: string;
  code: string;
  price: number;
}

// Available roles
export const AVAILABLE_ROLES = [
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
export const ROLE_HIERARCHY: Record<string, number> = {
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
export const ASSIGNABLE_ROLES: Record<string, string[]> = {
  'SUPERADMIN': AVAILABLE_ROLES,
  'ADMIN': AVAILABLE_ROLES.filter(r => r !== 'SUPERADMIN'),
  'STORE_MANAGER': ['OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF'],
};

// Get the highest role level from a list of roles
export const getHighestRoleLevel = (roles: string[]): number => {
  return Math.max(...roles.map(r => ROLE_HIERARCHY[r] || 0));
};

// Category definitions
export const CATEGORY_DEFINITIONS: Category[] = [
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

// ============================================================================
// IMS 2.0 - Settings Shared Types & Constants
// ============================================================================

export type SettingsTab =
  | 'profile'
  | 'business'
  | 'users'
  | 'categories'
  | 'brands'
  | 'lens-master'
  | 'lens-enums'
  | 'catalog-dictionary'
  | 'discounts'
  | 'tax-invoice'
  | 'hsn-rates'
  | 'tds-rates'
  | 'notifications'
  | 'reminders'
  | 'integrations'
  | 'printers'
  | 'audit-logs'
  | 'approvals'
  | 'agents'
  | 'loyalty'
  | 'lens-pricing'
  | 'policies'
  | 'refund-policy'
  | 'shopify-live-sync'
  | 'rules'
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
  entityId?: string | null;
}

export interface UserData {
  id: string;
  username: string;
  email: string;
  fullName: string;
  phone: string;
  roles: string[];
  accessibleStores: string[];
  /** Per-user OVERRIDE as stored (undefined = none: the role standard applies). */
  discountCap?: number;
  /** The cap the server actually enforces (role standard vs override). */
  effectiveDiscountCap?: number;
  isActive: boolean;
  createdAt: string;
  /** Per-user deny-only module override (canonical module key -> bool). A key
   *  set to false hides + route-blocks that module for the user on top of their
   *  role. Loaded from the backend `module_access` field; sent back on save. */
  moduleAccess?: Record<string, boolean>;
  /** Per-user CAPABILITY override (council ruling sec.2). Two-sided
   *  `{ grant: { <cap>: true }, deny: { <cap>: true } }`. Loaded from / sent
   *  back to the backend `permissions` field. Absent/empty => DARK. */
  permissions?: { grant?: Record<string, boolean>; deny?: Record<string, boolean> };
}

// NO hsnCode/gstRate here: HSN + GST are SERVER-FED (constants/gstRuntime reads
// GET /products/gst-rates). This table used to carry hand-typed copies that
// contradicted the server on 7 of 13 rows and were rendered as fact on the
// Settings screen.
export interface Category {
  code: string;
  name: string;
  shortName: string;
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
  /** Default Shopify-sync INTENT stamped on new products of this brand
   *  (nothing pushes from IMS -- the BVI app owns Shopify). */
  syncToShopifyDefault: boolean;
  /** SUPERADMIN hard block: the brand has not allowed online selling. Wins
   *  over syncToShopifyDefault in the server's one website gate. */
  neverSellOnline: boolean;
  /** Products in the master matching this brand (backend enrichment;
   *  undefined when the count aggregation was unavailable). */
  productCount?: number;
  /** Last edit time (ISO, from the server's updated_at); undefined if none. */
  updatedAt?: string;
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

// Role hierarchy - higher index = higher privilege. Same ORDER as the backend
// ROLE_LEVEL (services/user_roles.py). SALES_CASHIER is retained only so a
// legacy user still carrying the deprecated role renders/levels correctly; it is
// not in the catalogue below.
export const ROLE_HIERARCHY: Record<string, number> = {
  'SUPERADMIN': 10,
  'ADMIN': 9,
  'AREA_MANAGER': 7,
  'STORE_MANAGER': 6,
  'ACCOUNTANT': 5,
  'CATALOG_MANAGER': 5,
  'OPTOMETRIST': 4,
  'CASHIER': 3,
  'SALES_CASHIER': 2, // deprecated alias of SALES_STAFF (same effective level)
  'SALES_STAFF': 2,
  'WORKSHOP_STAFF': 2,
  'DESIGN_MANAGER': 2,
  'INVESTOR': 1,
};

// THE staff role catalogue (L1-F04): User Management and Staff Onboarding both
// read this one list, so the two doors can never offer different roles again.
// Plain-English labels because the owner is not a developer. `geoFenced` marks
// the store-staff tiers whose login is fenced to their assigned store.
export interface RoleOption {
  id: string;
  label: string;
  desc: string;
  level: number;
  geoFenced: boolean;
}

export const ROLE_CATALOGUE: RoleOption[] = [
  // Sales Cashier was merged into Sales Staff (backlog #12) -- one floor-sales
  // role now: rings up sales, takes payments, opens the drawer, discount up to 10%.
  { id: 'SALES_STAFF', label: 'Sales Staff', desc: 'Rings up sales, takes payments, opens the cash drawer, and searches the catalogue. Discount up to 10%.', level: ROLE_HIERARCHY.SALES_STAFF, geoFenced: true },
  { id: 'CASHIER', label: 'Cashier', desc: 'Takes payments only — no selling, no discounts.', level: ROLE_HIERARCHY.CASHIER, geoFenced: true },
  { id: 'OPTOMETRIST', label: 'Optometrist', desc: 'Runs eye tests and writes prescriptions. No selling.', level: ROLE_HIERARCHY.OPTOMETRIST, geoFenced: true },
  { id: 'WORKSHOP_STAFF', label: 'Workshop / Fitting', desc: 'Updates job status — frame fitting, lens mounting. No selling.', level: ROLE_HIERARCHY.WORKSHOP_STAFF, geoFenced: true },
  { id: 'STORE_MANAGER', label: 'Store Manager', desc: 'Runs one store end-to-end. Discount up to 20%.', level: ROLE_HIERARCHY.STORE_MANAGER, geoFenced: false },
  { id: 'ACCOUNTANT', label: 'Accountant', desc: 'Finance, GST and reconciliation. No POS or inventory.', level: ROLE_HIERARCHY.ACCOUNTANT, geoFenced: false },
  { id: 'CATALOG_MANAGER', label: 'Catalog Manager', desc: 'Owns the head-office product catalogue and pricing.', level: ROLE_HIERARCHY.CATALOG_MANAGER, geoFenced: false },
  { id: 'DESIGN_MANAGER', label: 'Design Manager', desc: 'Online-store design work: product photos, collections and menus. No selling.', level: ROLE_HIERARCHY.DESIGN_MANAGER, geoFenced: false },
  { id: 'AREA_MANAGER', label: 'Area Manager', desc: 'Oversees several stores. Discount up to 25%.', level: ROLE_HIERARCHY.AREA_MANAGER, geoFenced: false },
  { id: 'ADMIN', label: 'Admin (Director)', desc: 'Head-office administration across all stores and staff.', level: ROLE_HIERARCHY.ADMIN, geoFenced: false },
  { id: 'SUPERADMIN', label: 'Superadmin (CEO)', desc: 'Full control of the entire system, including AI.', level: ROLE_HIERARCHY.SUPERADMIN, geoFenced: false },
  { id: 'INVESTOR', label: 'Investor (read-only)', desc: 'Sees reports and figures only. Cannot change anything.', level: ROLE_HIERARCHY.INVESTOR, geoFenced: false },
];

export const ROLE_LABEL: Record<string, string> = Object.fromEntries(
  ROLE_CATALOGUE.map((r) => [r.id, r.label]),
);

// Every recognised role, for the User Management role FILTER (so an existing
// Cashier / Design Manager / Investor can be found).
export const AVAILABLE_ROLES = ROLE_CATALOGUE.map((r) => r.id);

/** The roles an actor may give someone, in BOTH staff doors. Mirrors the server:
 *  POST/PUT /users are ADMIN/SUPERADMIN only (rbac_policy), and can_assign_roles
 *  lets an actor assign only roles at or below their own level. The read-only
 *  Investor seat is offered to the SUPERADMIN only. */
export function assignableRolesFor(actorRoles: string[] | null | undefined): RoleOption[] {
  const roles = actorRoles || [];
  if (roles.includes('SUPERADMIN')) return ROLE_CATALOGUE;
  if (!roles.includes('ADMIN')) return [];
  return ROLE_CATALOGUE.filter((r) => r.level <= ROLE_HIERARCHY.ADMIN && r.id !== 'INVESTOR');
}

// Get the highest role level from a list of roles
export const getHighestRoleLevel = (roles: string[]): number => {
  return Math.max(...roles.map(r => ROLE_HIERARCHY[r] || 0));
};

// Category definitions (codes, names, attributes ONLY -- HSN/GST come off the
// server per the interface note above).
export const CATEGORY_DEFINITIONS: Category[] = [
  { code: 'FR', name: 'Frame', shortName: 'Spectacles', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength'], isActive: true },
  { code: 'SG', name: 'Sunglasses', shortName: 'Sunglasses', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength'], isActive: true },
  { code: 'CL', name: 'Contact Lens', shortName: 'Contact Lens', attributes: ['brandName', 'subbrand', 'modelNo', 'colourName', 'power', 'pack', 'expiryDate'], isActive: true },
  { code: 'LS', name: 'Optical Lens', shortName: 'Lens', attributes: ['brandName', 'subbrand', 'index', 'coating', 'addOn1', 'addOn2', 'addOn3', 'lensCategory'], isActive: true },
  { code: 'RG', name: 'Reading Glasses', shortName: 'Readers', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'power'], isActive: true },
  { code: 'WT', name: 'Wrist Watch', shortName: 'Watch', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'dialColour', 'beltColour', 'dialSize', 'beltSize', 'watchCategory'], isActive: true },
  { code: 'CK', name: 'Clock', shortName: 'Clock', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'dialColour', 'bodyColour', 'dialSize', 'batterySize', 'clockCategory'], isActive: true },
  { code: 'HA', name: 'Hearing Aid', shortName: 'Hearing Aid', attributes: ['brandName', 'subbrand', 'modelNo', 'serialNo', 'machineCapacity', 'machineType'], isActive: true },
  { code: 'SMTSG', name: 'Smart Sunglasses', shortName: 'Smart Sunglasses', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'yearOfLaunch'], isActive: true },
  { code: 'SMTFR', name: 'Smart Glasses', shortName: 'Smart Glasses', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'lensSize', 'bridgeWidth', 'templeLength', 'yearOfLaunch'], isActive: true },
  { code: 'SMTWT', name: 'Smart Watch', shortName: 'Smart Watch', attributes: ['brandName', 'subbrand', 'modelNo', 'colourCode', 'bodyColour', 'beltColour', 'dialSize', 'beltSize', 'yearOfLaunch'], isActive: true },
  { code: 'ACC', name: 'Accessories', shortName: 'Accessories', attributes: ['brandName', 'subbrand', 'modelNo', 'size', 'pack', 'expiryDate', 'addOn1'], isActive: true },
  { code: 'SVC', name: 'Service', shortName: 'Repair/Service', attributes: ['serviceName', 'serviceType', 'estimatedTime'], isActive: true },
];

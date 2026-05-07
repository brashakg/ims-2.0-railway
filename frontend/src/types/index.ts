// ============================================================================
// IMS 2.0 - TypeScript Type Definitions
// ============================================================================

// Brands
export type Brand = 'BETTER_VISION' | 'WIZOPT';

// User Roles - matching backend exactly
export type UserRole =
  | 'SUPERADMIN'
  | 'ADMIN'
  | 'AREA_MANAGER'
  | 'STORE_MANAGER'
  | 'ACCOUNTANT'
  | 'CATALOG_MANAGER'
  | 'OPTOMETRIST'
  | 'CASHIER'
  | 'SALES_CASHIER'
  | 'SALES_STAFF'
  | 'WORKSHOP_STAFF';

// Product Categories - complete list
export type ProductCategory =
  | 'FRAME'
  | 'FR'
  | 'SUNGLASS'
  | 'SG'
  | 'READING_GLASSES'
  | 'RG'
  | 'OPTICAL_LENS'
  | 'LS'
  | 'CONTACT_LENS'
  | 'CL'
  | 'COLORED_CONTACT_LENS'
  | 'WATCH'
  | 'WT'
  | 'SMARTWATCH'
  | 'SMTWT'
  | 'SMARTGLASSES'
  | 'SMTFR'
  | 'SMTSG'
  | 'WALL_CLOCK'
  | 'CK'
  | 'HEARING_AID'
  | 'HA'
  | 'ACCESSORIES'
  | 'ACC'
  | 'SERVICES'
  | 'SRV';

// ============================================================================
// Auth Types
// ============================================================================

export interface User {
  id: string;
  email: string;
  name: string;
  phone: string;
  roles: UserRole[];
  activeRole: UserRole;
  storeIds: string[];
  activeStoreId: string;
  discountCap: number;
  isActive: boolean;
  geoRestricted: boolean;
  createdAt: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

export interface LoginCredentials {
  username: string;
  password: string;
  store_id?: string;
  latitude?: number;
  longitude?: number;
}

export interface LoginResponse {
  success: boolean;
  token?: string;
  user?: User;
  message?: string;
  requiresStoreSelection?: boolean;
  availableStores?: Store[];
}

// ============================================================================
// Store Types
// ============================================================================

export interface Store {
  id: string;
  storeCode: string;
  storeName: string;
  brand: Brand;
  gstin: string;
  address: string;
  city: string;
  state: string;
  stateCode: string;
  pincode: string;
  latitude: number;
  longitude: number;
  geoFenceRadius: number;
  isActive: boolean;
  isHQ: boolean;
  enabledCategories: ProductCategory[];
  openingTime: string;
  closingTime: string;
}

// ============================================================================
// Product Types
// ============================================================================

export interface Product {
  id: string;
  sku: string;
  barcode?: string; // Optional barcode for product
  category: ProductCategory;
  brand: string;
  model: string;
  variant: string;
  name: string;
  description: string;
  mrp: number;
  offerPrice: number;
  discountCategory: 'MASS' | 'PREMIUM' | 'LUXURY' | 'SERVICE';
  hsnCode: string;
  gstRate: number;
  attributes: Record<string, string>;
  images: string[];
  isActive: boolean;
  createdAt: string;
}

export interface StockUnit {
  id: string;
  productId: string;
  storeId: string;
  barcode: string;
  quantity: number;
  reservedQuantity: number;
  locationCode: string;
  batchNumber?: string;
  expiryDate?: string;
  status: 'AVAILABLE' | 'RESERVED' | 'SOLD' | 'TRANSFERRED' | 'DAMAGED';
  barcodeprinted: boolean;
}

// ============================================================================
// Customer Types
// ============================================================================

export interface Customer {
  id: string;
  name: string;
  phone: string;
  email?: string;
  customerType: 'B2C' | 'B2B';
  gstNumber?: string;
  address?: string;
  city?: string;
  state?: string;
  pincode?: string;
  patients: Patient[];
  createdAt: string;
}

export interface Patient {
  id: string;
  customerId: string;
  name: string;
  relation?: string;
  dateOfBirth?: string;
  phone?: string;
}

// ============================================================================
// Prescription Types
// ============================================================================

export interface EyePower {
  sphere: number;
  cylinder: number | null;
  axis: number | null;
  add: number | null;
  pd: number;
  va?: string;
}

export interface Prescription {
  id: string;
  patientId: string;
  customerId: string;
  storeId: string;
  optometristId?: string;
  optometristName?: string;
  testDate: string;
  rightEye: EyePower;
  leftEye: EyePower;
  recommendation?: string;
  status: 'PENDING' | 'COMPLETED' | 'EXTERNAL';
  isExternal?: boolean;
  externalSource?: string;
  validityMonths?: number;
  createdAt: string;
  updatedAt: string;
}

// ============================================================================
// Order Types
// ============================================================================

export type OrderStatus =
  | 'DRAFT'
  | 'CONFIRMED'
  | 'PROCESSING'
  | 'READY'
  | 'DELIVERED'
  | 'CANCELLED';

export type PaymentStatus = 'PENDING' | 'PARTIAL' | 'PAID';

export type PaymentMode =
  | 'CASH'
  | 'UPI'
  | 'CARD'
  | 'BANK_TRANSFER'
  | 'EMI'
  | 'CREDIT'
  | 'GIFT_VOUCHER';

// Cart Item for POS (before order is created)
export interface CartItem {
  id: string;
  productId: string;
  productName: string;
  sku: string;
  barcode?: string;
  category: ProductCategory;
  itemType?: ProductCategory | 'LENS' | 'SERVICE';
  brand?: string;
  quantity: number;
  mrp: number;
  offerPrice: number;
  unitPrice: number;
  discountPercent: number;
  discountAmount: number;
  finalPrice: number;
  gstRate?: number;
  hsnCode?: string;
  prescriptionId?: string;
  requiresPrescription?: boolean;
  prescriptionLinked?: boolean;
  stockId?: string;
}

export interface OrderItem {
  id: string;
  itemType: ProductCategory | 'LENS' | 'SERVICE';
  productId: string;
  productName: string;
  sku: string;
  quantity: number;
  unitPrice: number;
  discountPercent: number;
  discountAmount: number;
  finalPrice: number;
  prescriptionId?: string;
}

export interface Payment {
  id: string;
  mode: PaymentMode;
  amount: number;
  reference?: string;
  paidAt: string;
}

export interface StatusHistory {
  status: OrderStatus;
  timestamp: string;
  changedBy: string;
}

export interface Order {
  id: string;
  orderNumber: string;
  storeId: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  patientId?: string;
  patientName?: string;
  items: OrderItem[];
  payments: Payment[];
  subtotal: number;
  totalDiscount: number;
  taxAmount: number;
  grandTotal: number;
  amountPaid: number;
  balanceDue: number;
  orderStatus: OrderStatus;
  paymentStatus: PaymentStatus;
  createdBy: string;
  createdAt: string;
  deliveredAt?: string;
  statusHistory?: StatusHistory[];
}

// ============================================================================
// Workshop Types
// ============================================================================

export type JobStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'QC_FAILED'
  | 'READY'
  | 'DELIVERED'
  // Legacy statuses for backwards compatibility
  | 'CREATED'
  | 'LENS_ORDERED'
  | 'LENS_RECEIVED'
  | 'QC_PENDING'
  | 'QC_PASSED'
  | 'CANCELLED';

export type JobPriority = 'NORMAL' | 'EXPRESS' | 'URGENT';

export interface WorkshopJob {
  id: string;
  jobNumber: string;
  jobType: string;
  orderId: string;
  orderNumber: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  storeId: string;
  frameBarcode?: string;
  frameName?: string;
  prescriptionId?: string;
  status: JobStatus;
  priority: JobPriority;
  assignedTo?: string;
  assignedName?: string;
  expectedDate: string;
  promisedDate: string;
  completedAt?: string;
  deliveredAt?: string;
  notes?: string;
  qcNotes?: string;
}

// ============================================================================
// HR Types
// ============================================================================

export interface Attendance {
  id: string;
  userId: string;
  userName: string;
  storeId: string;
  date: string;
  checkInTime?: string;
  checkOutTime?: string;
  checkInLat?: number;
  checkInLon?: number;
  lateMinutes: number;
  status: 'PRESENT' | 'ABSENT' | 'HALF_DAY' | 'LEAVE';
}

export interface Leave {
  id: string;
  userId: string;
  userName: string;
  leaveType: string;
  startDate: string;
  endDate: string;
  reason: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED';
  approvedBy?: string;
}

// ============================================================================
// API Response Types
// ============================================================================

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  message?: string;
  error?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

// ============================================================================
// Dashboard Types
// ============================================================================

export interface DashboardStats {
  todaySales: number;
  todayOrders: number;
  pendingJobs: number;
  lowStockItems: number;
  todayFootfall: number;
  monthSales: number;
  monthTarget: number;
  targetAchievement: number;
}

export interface SalesTrend {
  date: string;
  amount: number;
  orderCount: number;
}

export interface TopProduct {
  productId: string;
  productName: string;
  category: ProductCategory;
  quantity: number;
  revenue: number;
}


// ============================================================================
// WALKOUTS (Pune Incentive Module i)
// ============================================================================

export type AgeGroup =
  | '<15' | '15-25' | '26-35' | '36-45' | '46-55' | '56-65' | '65+';

export type WalkoutGender = 'MALE' | 'FEMALE' | 'OTHER';

export type WalkoutProductCategory =
  | 'FRAME' | 'SUNGLASS' | 'WATCH' | 'CLOCK'
  | 'LENS' | 'CONTACT LENS' | 'ACCESSORY' | 'OTHER';

export type YesNo = 'YES' | 'NO';

export type WalkoutPriceRange =
  | '<1000' | '1000-2000' | '2000-3000' | '3000-5000'
  | '5000-10000' | '10000-20000' | '20000-50000' | '50000+';

export type WalkoutReason =
  | 'BUDGET/PRICE' | 'COLLECTION' | 'COLOR' | 'BRAND' | 'ENQUIRY ONLY'
  | 'STAFF BEHAVIOUR' | 'NOT AVAILABLE' | 'STYLE/DESIGN' | 'FIT/SIZE' | 'OTHER';

export type PurchasePlan =
  | 'NEXT DAY' | '1-7 DAYS' | '8-15 DAYS' | '16-30 DAYS'
  | 'AFTER A MONTH' | 'UNDECIDED';

export type FollowUpMode = 'CALL' | 'WHATSAPP' | 'SMS' | 'EMAIL' | 'IN-PERSON';
export type FollowUpStatus =
  | 'PENDING' | 'DONE' | 'NOT REACHABLE' | 'NOT REQUIRED' | 'ESCALATED';
export type WalkoutResultValue = 'DUE' | 'NEGATIVE' | 'CONVERTED';

export interface WalkoutFollowUp {
  round: number;
  scheduled_date?: string;
  scheduled_time?: string;
  mode?: FollowUpMode;
  supervisor_id?: string | null;
  supervisor_name?: string | null;
  status: FollowUpStatus;
  notes?: string;
  completed_at?: string | null;
  completed_by?: string | null;
  escalation_task_id?: string | null;
}

export interface CreateFollowUpRequest {
  round: 1 | 2;
  scheduled_date: string;       // YYYY-MM-DD
  scheduled_time?: string;      // HH:MM
  mode: FollowUpMode;
  supervisor_id?: string;
  notes?: string;
}

export interface UpdateFollowUpRequest {
  status?: FollowUpStatus;
  notes?: string;
  scheduled_date?: string;
  scheduled_time?: string;
  mode?: FollowUpMode;
}

export interface SetWalkoutResultRequest {
  result: WalkoutResultValue;
  converted_order_id?: string;
}

export interface FollowUpDueRow extends WalkoutFollowUp {
  walkout_id: string;
  store_id: string;
  customer_name: string;
  mobile: string;
  sales_person_id: string;
  sales_person_name?: string;
}

export interface FollowUpsDueResponse {
  items: FollowUpDueRow[];
  as_of: string;
}

export const FOLLOWUP_MODES: FollowUpMode[] = [
  'CALL', 'WHATSAPP', 'SMS', 'EMAIL', 'IN-PERSON',
];
export const FOLLOWUP_STATUSES: FollowUpStatus[] = [
  'PENDING', 'DONE', 'NOT REACHABLE', 'NOT REQUIRED', 'ESCALATED',
];

export interface Walkout {
  walkout_id: string;
  store_id: string;
  date: string;
  date_str: string;
  customer_id: string | null;
  customer_name: string;
  mobile: string;
  age_group: AgeGroup;
  gender: WalkoutGender;
  product_interested: WalkoutProductCategory;
  has_prescription: YesNo;
  displayed_price_range: WalkoutPriceRange;
  required_price_range: WalkoutPriceRange;
  primary_walkout_reason: WalkoutReason;
  secondary_walkout_reason: WalkoutReason | null;
  brand_interest: string;
  competitor_mentioned: string;
  purchase_planned_in: PurchasePlan;
  sales_person_id: string;
  sales_person_name?: string;
  followups: WalkoutFollowUp[];
  result: 'DUE' | 'NEGATIVE' | 'CONVERTED' | null;
  result_set_at: string | null;
  converted_order_id: string | null;
  action_remarks: string;
  created_at: string;
  created_by: string;
  updated_at: string;
}

export interface CreateWalkoutRequest {
  customer_name: string;
  mobile: string;
  age_group: AgeGroup;
  gender: WalkoutGender;
  product_interested: WalkoutProductCategory;
  has_prescription: YesNo;
  displayed_price_range: WalkoutPriceRange;
  required_price_range: WalkoutPriceRange;
  primary_walkout_reason: WalkoutReason;
  secondary_walkout_reason?: WalkoutReason;
  brand_interest?: string;
  competitor_mentioned?: string;
  purchase_planned_in: PurchasePlan;
  sales_person_id: string;
  action_remarks?: string;
  date?: string;
}

// Phase 2 — partial update + list types
export interface UpdateWalkoutRequest {
  customer_name?: string;
  mobile?: string;
  age_group?: AgeGroup;
  gender?: WalkoutGender;
  product_interested?: WalkoutProductCategory;
  has_prescription?: YesNo;
  displayed_price_range?: WalkoutPriceRange;
  required_price_range?: WalkoutPriceRange;
  primary_walkout_reason?: WalkoutReason;
  secondary_walkout_reason?: WalkoutReason | null;
  brand_interest?: string;
  competitor_mentioned?: string;
  purchase_planned_in?: PurchasePlan;
  sales_person_id?: string;
  action_remarks?: string;
}

export interface ListWalkoutsParams {
  date_from?: string;
  date_to?: string;
  sales_person_id?: string;
  primary_walkout_reason?: WalkoutReason;
  result?: 'DUE' | 'NEGATIVE' | 'CONVERTED' | 'none';
  store_id?: string;
  skip?: number;
  limit?: number;
}

export interface ListWalkoutsResponse {
  items: Walkout[];
  total: number;
  skip: number;
  limit: number;
}

// Frozen enum option arrays — single source of truth for the
// WalkoutIntakeModal dropdowns. Order matches the Excel sheet.
export const WALKOUT_AGE_GROUPS: AgeGroup[] = [
  '<15', '15-25', '26-35', '36-45', '46-55', '56-65', '65+',
];
export const WALKOUT_GENDERS: WalkoutGender[] = ['MALE', 'FEMALE', 'OTHER'];
export const WALKOUT_PRODUCT_CATEGORIES: WalkoutProductCategory[] = [
  'FRAME', 'SUNGLASS', 'WATCH', 'CLOCK',
  'LENS', 'CONTACT LENS', 'ACCESSORY', 'OTHER',
];
export const WALKOUT_PRICE_RANGES: WalkoutPriceRange[] = [
  '<1000', '1000-2000', '2000-3000', '3000-5000',
  '5000-10000', '10000-20000', '20000-50000', '50000+',
];
export const WALKOUT_REASONS: WalkoutReason[] = [
  'BUDGET/PRICE', 'COLLECTION', 'COLOR', 'BRAND', 'ENQUIRY ONLY',
  'STAFF BEHAVIOUR', 'NOT AVAILABLE', 'STYLE/DESIGN', 'FIT/SIZE', 'OTHER',
];
export const WALKOUT_PURCHASE_PLANS: PurchasePlan[] = [
  'NEXT DAY', '1-7 DAYS', '8-15 DAYS', '16-30 DAYS',
  'AFTER A MONTH', 'UNDECIDED',
];

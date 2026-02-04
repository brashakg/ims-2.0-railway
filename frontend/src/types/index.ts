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
  | 'IN_PROGRESS'
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
}

// ============================================================================
// Workshop Types
// ============================================================================

export type JobStatus =
  | 'CREATED'
  | 'LENS_ORDERED'
  | 'LENS_RECEIVED'
  | 'IN_PROGRESS'
  | 'QC_PENDING'
  | 'QC_PASSED'
  | 'QC_FAILED'
  | 'READY'
  | 'DELIVERED'
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

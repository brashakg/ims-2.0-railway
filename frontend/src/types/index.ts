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
  | 'DESIGN_MANAGER'  // 13th role (BVI merge): e-commerce image/design queue
                      // owner. Forward-declared here for the Online Store
                      // module; backend RBAC matrix gains it in a later phase.
  | 'OPTOMETRIST'
  | 'CASHIER'
  | 'SALES_CASHIER'  // DEPRECATED: merged into SALES_STAFF (backlog #12). Kept
                     // in the union so a legacy user object still type-checks;
                     // the backend normalizes it to SALES_STAFF. Not assignable.
  | 'SALES_STAFF'
  | 'WORKSHOP_STAFF'
  | 'INVESTOR';  // 12th canonical role (May 2026): silent investor /
                 // franchise partner accountant. Read-only across the
                 // entire app; backend middleware blocks all writes.

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
  | 'CCL'
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
  /** When true, the user signed in with an admin-set temporary password and
   *  must change it before using the app. Set by the backend on user-create /
   *  password-reset; cleared once they change it. The frontend gates the whole
   *  app on this flag (see AppLayout). */
  mustChangePassword?: boolean;
  /** Per-user module access -- a DENY-ONLY override layered on top of the role.
   *  Map of canonical module key -> boolean. A key set to `false` HIDES the
   *  module from the nav AND blocks its routes for this user, even when their
   *  role would otherwise allow it. Missing / `true` => role defaults apply.
   *  The role is always the ceiling: this can only restrict, never grant.
   *  See AuthContext.hasModuleAccess + ModuleContext. */
  moduleAccess?: Record<string, boolean>;
  /** Per-user CAPABILITY override (council ruling sec.2). Two-sided:
   *  `{ grant: { <cap>: true }, deny: { <cap>: true } }`. A grant ADDS a
   *  role-denied capability (capped server-side at the granting admin's level +
   *  inviolable business floors); a deny REMOVES a role-granted one. Deny always
   *  beats grant. Absent/empty => DARK: behaves exactly as the role baseline.
   *  See AuthContext.hasPermission. */
  permissions?: { grant?: Record<string, boolean>; deny?: Record<string, boolean> };
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
  // Marketing-consent flag (backend snake_case). Read by Customer 360
  // PreferencesTab and written by the consent toggle via
  // updateCustomer(id, { marketing_consent }).
  marketing_consent?: boolean;
  // POS-4: per-customer credit limit (khata). 0 = unlimited.
  credit_limit?: number;
  // F39: manager-approved free-form tags (e.g. "VIP", "Zeiss fan"). Feed the
  // NBA daily call list. Staff suggest; STORE_MANAGER+ approves.
  tags?: string[];
  // F40: personalised VIP churn-risk subdoc, written nightly by ORACLE for
  // VIP customers (LTV >= 1,00,000 AND >= 3 completed orders). Present only on
  // qualifying customers; surfaced by the Customer 360 VIP card. Shape mirrors
  // VipChurnRisk in services/api/crm.ts (kept inline to keep this module free
  // of service-layer imports).
  vip_churn_risk?: {
    usual_interval_days: number;
    last_purchase_days_ago: number;
    overdue_by_days: number;
    risk_score: number;
    risk_label: 'NONE' | 'WATCH' | 'HIGH';
    narrative: string | null;
  };
}

export interface Patient {
  id: string;
  customerId: string;
  name: string;
  relation?: string;
  dateOfBirth?: string;
  phone?: string;
  // BILL-TO-MEMBER P1: the account holder's member row is flagged is_primary on
  // the backend. Surfaced here so POS can default-select the Primary member and
  // badge it first in a multi-member account.
  isPrimary?: boolean;
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

export type PaymentStatus = 'PENDING' | 'PARTIAL' | 'PAID' | 'UNPAID' | 'CREDIT' | 'REFUNDED';

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
  /** Public order-tracking token — powers the no-login /track/{token} link
   *  and the staff-facing tracking QR. Snake-case on the wire (tracking_token);
   *  the axios camelCase aliaser also exposes it as trackingToken. */
  trackingToken?: string;
  tracking_token?: string;
}

// ============================================================================
// Workshop Types
// ============================================================================

export type JobStatus =
  | 'PENDING'
  | 'IN_PROGRESS'   // canonical backend status (was PROCESSING)
  | 'PROCESSING'    // legacy frontend alias — kept for STATUS_CONFIG/backward compat
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
// F45 D6 -- RESCHEDULED + NOT INTERESTED are additive (existing values kept).
export type FollowUpStatus =
  | 'PENDING' | 'DONE' | 'NOT REACHABLE' | 'NOT REQUIRED' | 'ESCALATED'
  | 'RESCHEDULED' | 'NOT INTERESTED';
export type FollowUpApprovalStatus =
  | 'PENDING_APPROVAL' | 'APPROVED' | 'REJECTED';
export type FollowUpApprovalDecision = 'APPROVED' | 'REJECTED';
// F45 D6 -- WON / LOST are additive Excel-alignment outcomes.
export type WalkoutResultValue = 'DUE' | 'NEGATIVE' | 'CONVERTED' | 'WON' | 'LOST';

// F45 D3 -- reason-driven follow-up policy computed on create.
export type WalkoutPolicyAction =
  | 'PROMO_VOUCHER' | 'RESTOCK_WATCH' | 'MANAGER_ESCALATE' | 'STANDARD_FU' | null;

export interface WalkoutPolicySuggestion {
  action: WalkoutPolicyAction;
  voucher_eligible: boolean;
  restock_watch: boolean;
  escalate_immediate: boolean;
  suggested_fu_channel: 'CALL' | 'WHATSAPP' | 'SMS';
  escalation_task_id?: string;
}

// F45 D2 -- 50/50 sale-credit split written on CONVERTED.
export interface WalkoutSaleCredit {
  credit_type: 'LOGGING' | 'CLOSING';
  user_id: string;
  user_name?: string | null;
  pct: number;
  order_id: string;
  credited_at?: string;
  month_key?: string;
}

// F45 D5 -- POS soft-block compliance counter (read-only).
export interface WalkoutPosComplianceCheck {
  open_count: number;
  overdue_count: number;
  oldest_open_date: string | null;
}

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
  // Manager approval (anti-fake-closure). approval_required + status
  // are only meaningful when status === 'DONE'.
  approval_required?: boolean;
  approval_status?: FollowUpApprovalStatus | null;
  approved_by_user_id?: string | null;
  approved_by_name?: string | null;
  approved_at?: string | null;
  manager_note?: string | null;
}

export interface CreateFollowUpRequest {
  round: 1 | 2 | 3;
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

export interface ApproveFollowUpRequest {
  decision: FollowUpApprovalDecision;
  manager_note?: string;
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
  'RESCHEDULED', 'NOT INTERESTED',
];

// Phase 4 — walk-in counter + dashboard types
// N3 footfall status enum: PENDING (no entry yet) / PARTIAL (some staff
// missing) / COMPLETE (every expected staff has a count).
export type FootfallEntryStatus = 'PENDING' | 'PARTIAL' | 'COMPLETE';

export interface WalkinTodayResponse {
  store_id: string;
  date_str: string;
  pos_auto_count: number;
  manual_topup: number;
  total: number;
  per_staff: Record<string, number>;
  // N3 — footfall capture status (PENDING / PARTIAL / COMPLETE).
  entry_status?: FootfallEntryStatus;
  per_staff_log?: Array<{
    staff_id: string;
    old_val: number | null;
    new_val: number;
    updated_by: string;
    updated_at: string;
    reason?: string | null;
  }>;
}

export interface ManualTopupRequest {
  delta: number;
  reason: string;
  sales_person_id?: string;
}

// N3 — footfall capture status for a (store, date).
export interface WalkinStatusResponse {
  store_id: string;
  date_str: string;
  status: FootfallEntryStatus;
  staff_with_data: { staff_id: string; walk_ins: number }[];
  staff_missing: string[];
  total_walk_ins: number;
  store_conversion_pct: number | null;
}

// N3 — manager sets/updates one staff member's walk-in count for a day.
export interface PerStaffWalkinRequest {
  staff_id: string;
  walk_ins: number;
  date_str?: string;
  reason?: string;
}

// N3 — one row of the conversion feed (per salesperson, per day). The
// conversion_score is null + footfall_missing=true when walk-ins are missing.
export interface ConversionFeedRow {
  sales_person_id: string;
  name: string | null;
  walk_ins_today: number;
  walkouts_today: number;
  retro_conversions_today: number;
  conversion_score: number | null;
  footfall_missing: boolean;
}

export interface PerStaffCard {
  sales_person_id: string;
  sales_person_name: string | null;
  walkouts_mtd: number;
  walkouts_today: number;
  converted_mtd: number;
  walk_ins_today: number;
  walk_ins_mtd: number;
  fu_due_today: number;
  conversion_pct_mtd: number;
}

export interface PerStaffResponse {
  store_id: string;
  as_of: string;
  items: PerStaffCard[];
}

export interface TopReasonsResponse {
  store_id: string;
  days: number;
  items: { reason: string; count: number }[];
}

export interface ResultBreakdownResponse {
  store_id: string;
  days: number;
  total: number;
  buckets: { DUE: number; NEGATIVE: number; CONVERTED: number; no_result: number };
}

export interface FUStatusResponse {
  store_id: string;
  days: number;
  fu1: Record<string, number>;
  fu2: Record<string, number>;
}

// ============================================================================
// Pune Incentive Module (ii) — Daily Points
// ============================================================================

export type PointCategory =
  | 'attendance' | 'conversion' | 'task' | 'visufit'
  | 'punctuality' | 'behaviour' | 'kicker_1' | 'kicker_2' | 'reviews';

export interface DailyScores {
  attendance: number;
  conversion: number | null;
  task: number;
  visufit: number;
  punctuality: number;
  behaviour: number;
  kicker_1: number;
  kicker_2: number;
  reviews: number;
}

export interface CreateDailyPointsRequest {
  date: string;
  staff_id: string;
  scores: DailyScores;
  visufit_usage_pct_mtd?: number | null;
}

export interface BulkDailyPointsRequest {
  rows: CreateDailyPointsRequest[];
}

export interface EligibilityBand {
  min: number;
  max: number;
  value: number;
}

export interface PointsLog {
  log_id: string;
  store_id: string;
  date: string;
  date_str: string;
  staff_id: string;
  staff_name: string | null;
  attendance: number;
  conversion: number;
  task: number;
  visufit: number;
  punctuality: number;
  behaviour: number;
  kicker_1: number;
  kicker_2: number;
  reviews: number;
  total: number;
  eligibility: number;
  eligibility_thresholds_used: { bands: EligibilityBand[] };
  visufit_gate_applied: boolean;
  visufit_usage_pct_mtd: number | null;
  created_at: string;
  updated_at: string;
}

export interface BulkPointsResponse {
  saved: PointsLog[];
  failures: Array<{ staff_id: string; date: string; status_code: number; detail: string }>;
  saved_count: number;
  failed_count: number;
}

export interface DailyListResponse {
  items: PointsLog[];
  store_id: string;
  date_str: string;
}

export type LeaderboardScope = 'store' | 'area' | 'org';
export type LeaderboardTier = 'PODIUM' | 'CONTENDER' | 'BUILDING';

export interface MTDStaffEntry {
  staff_id: string;
  staff_name: string | null;
  days_logged: number;
  avg: {
    attendance: number; conversion: number; task: number; visufit: number;
    punctuality: number; behaviour: number; kicker_1: number; kicker_2: number;
    reviews: number; total: number;
  };
  eligibility_avg: number;
  // F33 — gamified display layer (server-computed; optional during deploy window)
  rank?: number;
  tier_label?: LeaderboardTier;
  title_earned?: string | null;
  badge_keys?: string[];
  rank_delta?: number | null;
}

export interface MTDResponse {
  store_id: string | null;
  scope?: LeaderboardScope;
  year: number;
  month: number;
  date_from: string;
  date_to: string;
  items: MTDStaffEntry[];
}

export interface LeaderboardResponse {
  store_id: string | null;
  scope?: LeaderboardScope;
  days: number;
  date_from: string;
  date_to: string;
  items: MTDStaffEntry[];
}

export interface LeaderboardCatalogItem {
  kind: 'title' | 'badge';
  key: string;
  label: string;
  description: string;
}

export interface LeaderboardTitlesResponse {
  titles: LeaderboardCatalogItem[];
  badges: LeaderboardCatalogItem[];
  tiers: LeaderboardTier[];
}

export interface LeaderboardConfig {
  enabled: boolean;
  scope_default: LeaderboardScope;
  show_titles: boolean;
  show_badges: boolean;
}

export interface SupervisorBonus {
  user_id: string;
  role: string;
  bonus_pct: Record<string, number>;
}

export interface IncentiveSettings {
  store_id: string;
  staff_weightages: Record<string, number>;
  eligibility_bands: EligibilityBand[];
  growth_targets: Record<string, number>;
  base_rates: Record<string, number>;
  discount_kill_threshold: number;
  discount_multipliers: Array<{ max_pct: number; multiplier: number }>;
  visufit_gate_threshold: number;
  visufit_gate_enabled: boolean;
  supervisor_bonuses: SupervisorBonus[];
  updated_at: string | null;
  updated_by: string | null;
}

// ============================================================================
// Pune Incentive Module (iii) — Payout
// ============================================================================

export type PayoutLevel = 'L1' | 'L2' | 'L3';
export type PayoutStatus = 'DRAFT' | 'LOCKED' | 'PAID';

export interface PayoutTargetEntry {
  growth: number;
  target: number;
  achieved: boolean;
}

export interface StaffPayout {
  user_id: string;
  name?: string | null;
  weightage: number;
  mtd_avg_total: number | null;
  eligibility: number;
  payout_by_level: Record<PayoutLevel, number>;
  total_payout: number;
  /** SC: Product-Incentive Kicker rupees folded in for the month. */
  product_incentive?: number;
  /** SC: total_payout + manager bonus (if any) + product_incentive. */
  total_with_kicker?: number;
}

export interface ManagerBonus {
  user_id: string;
  role?: string | null;
  name?: string | null;
  eligibility: number;
  bonus_pct: Record<PayoutLevel, number>;
  bonus_by_level: Record<PayoutLevel, number>;
  total_bonus: number;
}

export interface PayoutEnvelope {
  store_id: string;
  year: number;
  month: number;
  inputs: {
    last_year_sale: number;
    this_year_sale: number;
    avg_discount_pct: number;
    visufit_usage_pct: number;
  };
  targets: Record<PayoutLevel, PayoutTargetEntry>;
  best_level_achieved: PayoutLevel | null;
  discount_kill_active: boolean;
  multiplier: number;
  multiplier_tier: string;
  pools: Record<PayoutLevel, number>;
  total_team_pool: number;
  staff_payouts: StaffPayout[];
  manager_bonuses: ManagerBonus[];
  grand_total: {
    staff: number;
    manager: number;
    all: number;
    product_incentive?: number;
    all_with_kicker?: number;
  };
  /** SC: total Product-Incentive Kicker rupees for the store-month. */
  product_incentive_total?: number;
}

export interface PayoutSnapshot extends PayoutEnvelope {
  snapshot_id: string;
  status: PayoutStatus;
  locked_at: string | null;
  locked_by: string | null;
  paid_at: string | null;
  paid_by: string | null;
  created_at: string;
  updated_at: string;
  /** SC: stamped the first time a payroll-run consumes this snapshot (P0-4). */
  payroll_fed_at?: string | null;
  payroll_run_id?: string | null;
}

export interface PayrollFeedResponse {
  store_id: string;
  year: number;
  month: number;
  snapshot_id: string;
  status: PayoutStatus;
  /** { staff_id: total_incentive_rupees } -- the single payroll incentive source. */
  feed: Record<string, number>;
  payroll_fed_at: string | null;
  payroll_run_id: string | null;
}

export interface KickerEntry {
  entry_id: string;
  store_id: string;
  date_str: string;
  ym: string;
  staff_id: string;
  staff_name?: string | null;
  sku: string;
  brand: string;
  category: string;
  description?: string | null;
  order_id?: string | null;
  incentive_amount: number;
}

export interface KickerRollupResponse {
  store_id: string;
  ym: string;
  total: number;
  items: Array<{
    staff_id: string;
    staff_name?: string | null;
    total_rupees: number;
    sale_count: number;
    entries: KickerEntry[];
  }>;
}

export interface PreviewParams {
  year?: number;
  month?: number;
  last_year_sale?: number;
  this_year_sale?: number;
  avg_discount_pct?: number;
  visufit_usage_pct?: number;
  store_id?: string;
}

export interface LockSnapshotRequest {
  year: number;
  month: number;
  last_year_sale?: number;
  this_year_sale?: number;
  avg_discount_pct?: number;
  visufit_usage_pct?: number;
}

export interface SnapshotsListResponse {
  items: PayoutSnapshot[];
  store_id: string;
  year: number;
}

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
  result: WalkoutResultValue | null;
  result_set_at: string | null;
  converted_order_id: string | null;
  action_remarks: string;
  // F45 -- reason-driven policy (D3) + 50/50 sale-credit split (D2). Both are
  // additive: absent on legacy docs, populated on create / CONVERTED.
  policy_suggestion?: WalkoutPolicySuggestion;
  sale_credits?: WalkoutSaleCredit[];
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

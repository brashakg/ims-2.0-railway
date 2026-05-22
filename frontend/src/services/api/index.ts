// ============================================================================
// IMS 2.0 - API Service (barrel re-export)
// ============================================================================
// All domain-specific API modules are re-exported here so that existing
// imports from '../services/api' continue to work unchanged.

// Shared axios client
export { default, default as api } from './client';

// Auth
export { authApi } from './auth';

// Stores & Admin (stores, users, system)
export { storeApi, adminStoreApi, adminUserApi, adminSystemApi } from './stores';

// Products & Catalog (products, categories, brands, lens)
export { productApi, catalogApi, adminProductApi, adminCategoryApi, adminBrandApi, adminLensApi } from './products';
export type { OnlineStatus } from './products';

// Inventory & Vendors
export { inventoryApi, vendorsApi, reorderApi } from './inventory';

// Sales / Billing / Orders (orders, prescriptions, workshop, discounts)
export { orderApi, prescriptionApi, workshopApi, adminDiscountApi } from './sales';

// Customers
export { customerApi } from './customers';

// Reports & Analytics
export { reportsApi, analyticsApi } from './reports';

// HR / Payroll / Incentives / Tasks
export { hrApi, tasksApi } from './hr';

// Clinical / Eye Tests
export { clinicalApi } from './clinical';

// Expenses
export { expensesApi } from './expenses';

// Settings & Integrations
export { settingsApi, adminIntegrationApi } from './settings';

// Marketing Automation
export { marketingApi } from './marketing';

// Analytics V2
export { analyticsV2Api } from './analytics';

// Walkouts (Pune Incentive Module i)
export { walkoutsApi } from './walkouts';

// Daily Points (Pune Incentive Module ii) — distinct from the older
export { incentiveApi } from './incentive';

// Payout (Pune Incentive Module iii)
export { payoutApi } from './payout';

// Handoffs (user-to-user file handoff feature)
export { handoffsApi } from './handoffs';
export type {
  Handoff,
  HandoffRecipient,
  HandoffRecipientStatus,
  HandoffResponseValue,
  HandoffFileMeta,
  InboxItem,
  EligibleRecipient,
  DismissAction,
} from './handoffs';

// Customer Loyalty / Points engine
export { loyaltyApi } from './loyalty';
export type {
  LoyaltyAccount,
  LoyaltyTier,
  LoyaltyTxnType,
  LoyaltyTransaction,
  LoyaltySettings,
  LoyaltyAccountResponse,
  LoyaltyLedgerResponse,
  EarnRequest,
  EarnResponse,
  RedeemRequest,
  RedeemResponse,
  AdjustRequest,
} from './loyalty';

// Legal Entities (payroll / GST org structure)
export { entitiesApi } from './entities';
export type { Entity, GstinEntry, PtRegistration } from './entities';

// Payroll config (Structured-CTC salary master + Professional Tax slabs)
export { payrollApi, grossOf } from './payroll';
export type { SalaryConfig, PtSlab, OtherAllowance } from './payroll';

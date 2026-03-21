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
export { productApi, adminProductApi, adminCategoryApi, adminBrandApi, adminLensApi } from './products';

// Inventory & Vendors
export { inventoryApi, vendorsApi, reorderApi } from './inventory';

// Sales / Billing / Orders (orders, prescriptions, workshop, discounts)
export { orderApi, prescriptionApi, workshopApi, adminDiscountApi } from './sales';

// Customers
export { customerApi } from './customers';

// Reports & Analytics
export { reportsApi, analyticsApi } from './reports';

// HR / Payroll / Incentives / Tasks
export { hrApi, incentivesApi, tasksApi } from './hr';

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

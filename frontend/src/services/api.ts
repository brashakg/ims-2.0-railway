// ============================================================================
// IMS 2.0 - API Service (backwards-compatibility shim)
// ============================================================================
// This file re-exports everything from the new modular api/ directory so that
// existing imports like `import { orderApi } from '../services/api'` and
// `import api from '../services/api'` continue to work without changes.

export {
  default,
  authApi,
  storeApi,
  adminStoreApi,
  adminUserApi,
  adminSystemApi,
  productApi,
  adminProductApi,
  adminCategoryApi,
  adminBrandApi,
  adminLensApi,
  inventoryApi,
  vendorsApi,
  orderApi,
  prescriptionApi,
  workshopApi,
  adminDiscountApi,
  customerApi,
  reportsApi,
  analyticsApi,
  hrApi,
  incentivesApi,
  tasksApi,
  clinicalApi,
  settingsApi,
  adminIntegrationApi,
} from './api/index';

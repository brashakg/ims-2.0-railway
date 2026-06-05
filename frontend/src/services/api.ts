// ============================================================================
// IMS 2.0 - API Service (backwards-compatibility shim)
// ============================================================================
// This file re-exports everything from the new modular api/ directory so that
// existing imports like `import { orderApi } from '../services/api'` and
// `import api from '../services/api'` continue to work without changes.

export * from './api/index';
export { default } from './api/index';

export type { OnlineStatus } from './api/index';

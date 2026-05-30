/**
 * Shared constants for the IMS 2.0 E2E suite.
 *
 * These mirror what `backend/scripts/seed_e2e.py` seeds. They are the
 * independently-specified expected values (not derived from the running app),
 * so the assertions remain a real check, not a tautology.
 */

// Backend base. In CI the SPA is served same-origin behind a /api proxy, so
// E2E_API_URL points at the proxied path; default to the local backend direct.
export const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8000';

export const CREDENTIALS = {
  username: process.env.E2E_USERNAME ?? 'admin',
  password: process.env.E2E_PASSWORD ?? 'admin123',
} as const;

// Storage state path — written once by global-setup, reused by every test.
export const STORAGE_STATE = 'fixtures/.auth/user.json';

/**
 * Deterministic seed catalog (see seed_e2e.py). The E2E suite asserts against
 * these exact products so the math is reproducible across runs.
 */
export const SEED = {
  // Better Vision, Bokaro (Jharkhand) — carries a GSTIN so invoices generate.
  primaryStore: 'BV-BOK-01',
  // A second Jharkhand store for the store-switch spec.
  secondaryStore: 'BV-BOK-02',

  // 5% frame priced at exactly Rs 999 (the canonical inclusive-GST case).
  frame: {
    productId: 'e2e-frame-999',
    name: 'E2E Test Frame 999',
    category: 'FRAMES',
    itemType: 'FRAME',
    price: 999,
    gstRate: 5,
  },
  // 18% sunglass for the multi-rate cart.
  sunglass: {
    productId: 'e2e-sunglass-1180',
    name: 'E2E Test Sunglass 1180',
    category: 'SUNGLASSES',
    itemType: 'SUNGLASS',
    price: 1180,
    gstRate: 18,
  },
} as const;

/** GST pricing modes the backend may run in. */
export type GstMode = 'inclusive' | 'exclusive';

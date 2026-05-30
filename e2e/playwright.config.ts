import { defineConfig, devices } from '@playwright/test';

/**
 * IMS 2.0 — Playwright E2E config.
 *
 * Runs against a CI-seeded LOCAL stack. NEVER point this at production
 * (not the Vercel app, not the Railway API). `E2E_BASE_URL` is provided by
 * CI as http://localhost:4173 (a vite preview of the built SPA that proxies
 * /api -> the local uvicorn backend on :8000, so the whole suite is
 * same-origin and self-contained).
 */
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:4173';

export default defineConfig({
  testDir: './tests',
  // One auth login shared across specs (see fixtures/auth.setup.ts).
  globalSetup: './fixtures/global-setup.ts',
  // Fail the build if a test file is accidentally focused with test.only.
  forbidOnly: !!process.env.CI,
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  // Keep workers modest in CI — the backend is a single uvicorn process and
  // the suite mutates shared DB state (orders, store switches).
  workers: process.env.CI ? 1 : undefined,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // WebKit is opt-in (slower in CI, optional per the brief). Enable locally
    // or in a separate CI lane by setting E2E_WEBKIT=1.
    ...(process.env.E2E_WEBKIT
      ? [{ name: 'webkit', use: { ...devices['Desktop Safari'] } }]
      : []),
  ],
});

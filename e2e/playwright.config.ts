import { defineConfig, devices } from '@playwright/test';

/**
 * IMS 2.0 — Playwright E2E config.
 *
 * Runs against a CI-seeded LOCAL stack. NEVER point this at production
 * (not the Vercel app, not the Railway API). `E2E_BASE_URL` is provided by
 * CI as http://localhost:4173 (a vite preview of the built SPA that proxies
 * /api -> the local uvicorn backend on :8000, so the whole suite is
 * same-origin and self-contained).
 *
 * TWO PROJECTS, TWO WORKER COUNTS
 * -------------------------------
 * The layout probe is READ-ONLY: it navigates, measures geometry, asserts.
 * It never writes. The other specs DO write (orders, store switches) and
 * share one seeded database, so they must stay serial. Playwright has no
 * per-project `workers` setting (it is a global, config- or CLI-level knob),
 * so CI runs this config TWICE in the same job — `--project=layout
 * --workers=N` then `--project=stateful --workers=1`. Both steps live in the
 * `e2e` job, so both still report into the single `e2e` check.
 *
 * Splitting by FILENAME is what keeps that honest: `stateful` is the
 * complement of `layout`, so every spec file lands in exactly one project and
 * a new file can never be silently dropped. A spec that does not match the
 * read-only pattern falls into the SERIAL project — slower, never wrong.
 */
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:4173';

// Hard stop: this suite logs in, creates orders and switches stores. Pointing
// it at a deployed host would mutate real data. Refuse to start.
if (/vercel\.app|railway\.app|bettervision\.in|wizopt|myshopify\.com/i.test(baseURL)) {
  throw new Error(
    `E2E_BASE_URL points at a deployed host (${baseURL}). This suite mutates ` +
      `data and must only ever run against a local CI-seeded stack.`,
  );
}

/** Read-only specs: geometry probes that navigate and measure, nothing else.
 *  Name a new read-only spec `layout*.spec.ts` to land it in the parallel
 *  project; anything else falls into the serial one. */
// Read-only geometry specs: they navigate, open a popup and MEASURE. They
// never write, so they can run many-at-once, unlike the specs that mutate
// orders and store state. `popup-layout.spec.ts` belongs here too - it only
// clicks openers that were vetted as non-destructive at discovery time.
const READ_ONLY = /(^|[\/])(layout|popup)[\w.-]*\.spec\.ts$/;

export default defineConfig({
  testDir: './tests',
  // One auth login shared across specs (see fixtures/auth.setup.ts).
  globalSetup: './fixtures/global-setup.ts',
  // Fail the build if a test file is accidentally focused with test.only.
  forbidOnly: !!process.env.CI,
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  // Overridden per invocation from CI (see the two run steps in e2e.yml).
  // Default stays 1 so an unqualified `npx playwright test` is always safe.
  workers: process.env.CI ? 1 : undefined,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [
    ['list'],
    // Each invocation needs its own folder or the second overwrites the first.
    ['html', { open: 'never', outputFolder: process.env.PW_HTML_DIR ?? 'playwright-report' }],
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
      name: 'layout',
      testMatch: READ_ONLY,
      // One test now measures a screen at all seven widths, so it needs more
      // than the 60s a single-assertion test gets. Worst case is 7 x the
      // settle cap in layout.spec.ts plus the page load.
      timeout: 120_000,
      // Per-project outputDir so the two invocations cannot wipe each other's
      // traces (Playwright clears the outputDir of the projects it runs).
      outputDir: 'test-results/layout',
      use: {
        ...devices['Desktop Chrome'],
        // Geometry is deterministic, so this spec runs with retries: 0 — which
        // means `on-first-retry` would never produce a trace. Keep the trace,
        // drop the video: a trace has the DOM + the screenshot, and recording
        // video for every test in a many-hundred-test matrix is pure overhead.
        trace: 'retain-on-failure',
        video: 'off',
      },
    },
    {
      name: 'stateful',
      testIgnore: READ_ONLY,
      outputDir: 'test-results/stateful',
      use: { ...devices['Desktop Chrome'] },
    },
    // WebKit is opt-in (slower in CI, optional per the brief). Enable locally
    // or in a separate CI lane by setting E2E_WEBKIT=1.
    ...(process.env.E2E_WEBKIT
      ? [{ name: 'webkit', use: { ...devices['Desktop Safari'] } }]
      : []),
  ],
});

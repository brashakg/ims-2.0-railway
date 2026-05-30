/**
 * Custom test fixtures.
 *
 * - `page`/`context` come pre-authenticated via the saved storageState
 *   (see global-setup.ts), so specs skip the login screen.
 * - `api` is a backend client carrying its own bearer token for stored-value
 *   assertions and for seeding/inspecting orders directly.
 * - `mode` is the resolved GST pricing mode, so specs assert the matching
 *   expectation (inclusive vs exclusive) and stay green either way.
 */
import { test as base, expect } from '@playwright/test';
import { ApiClient, gstMode } from './api';
import { STORAGE_STATE, type GstMode } from './constants';

type Fixtures = {
  api: ApiClient;
  mode: GstMode;
};

export const test = base.extend<Fixtures>({
  // Every test runs inside the authenticated browser context.
  storageState: STORAGE_STATE,

  api: async ({}, use) => {
    const client = await ApiClient.login();
    await use(client);
    await client.dispose();
  },

  // Resolve once per test; cheap (single /health probe) and keeps specs
  // self-contained without a second global hook.
  mode: async ({}, use) => {
    await use(await gstMode());
  },
});

export { expect };

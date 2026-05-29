/**
 * Global setup: log in ONCE through the real UI and persist the browser
 * storage (localStorage.ims_token + ims_user) so every spec starts already
 * authenticated. This exercises the genuine login path once, then reuses it —
 * fast and faithful.
 */
import { chromium, type FullConfig, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { CREDENTIALS, STORAGE_STATE } from './constants';

async function globalSetup(config: FullConfig) {
  const { baseURL } = config.projects[0].use;
  const url = baseURL ?? 'http://localhost:4173';

  mkdirSync(dirname(STORAGE_STATE), { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    await page.goto(`${url}/login`, { waitUntil: 'domcontentloaded' });

    // First textbox = username, password input, then "Sign In".
    await page.locator('input').first().fill(CREDENTIALS.username);
    await page.locator('input[type="password"]').fill(CREDENTIALS.password);
    await page.getByRole('button', { name: /sign in/i }).click();

    // Login success drops a JWT into localStorage.ims_token and navigates off
    // /login. Wait for the token to actually land — that's the real signal.
    await expect
      .poll(
        async () => page.evaluate(() => localStorage.getItem('ims_token')),
        { message: 'ims_token never appeared after login', timeout: 30_000 }
      )
      .not.toBeNull();

    await page.context().storageState({ path: STORAGE_STATE });
  } finally {
    await browser.close();
  }
}

export default globalSetup;

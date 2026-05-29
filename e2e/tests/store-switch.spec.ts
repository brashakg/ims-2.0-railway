/**
 * Store switch re-issues the JWT (guards PR #327 — QA F9).
 *
 * Before #327 the topbar store pill changed the UI label but never re-issued
 * the token, so the JWT's active_store_id stayed on the old store and every
 * store-scoped query silently returned the WRONG store's data. The fix POSTs
 * /auth/switch-store/{id} and swaps localStorage.ims_token for a token whose
 * active_store_id is the new store.
 *
 * This spec asserts the token's active_store_id actually changes (decoded in
 * the browser), and that the backend re-scopes /orders to the new store.
 */
import { test, expect } from '../fixtures/test';
import { decodeJwt } from '../fixtures/api';
import { SEED } from '../fixtures/constants';

/** Read + decode the active_store_id from localStorage.ims_token, in-page. */
async function activeStoreFromToken(page: any): Promise<string | undefined> {
  const token = await page.evaluate(() => localStorage.getItem('ims_token'));
  if (!token) return undefined;
  return decodeJwt(token).active_store_id;
}

test.describe('Store switcher', () => {
  test('switching active store re-issues the JWT and re-scopes data', async ({
    page,
    api,
  }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // The store pill only renders for multi-store users (admin = all stores).
    const pill = page.locator('button.store-pill');
    await expect(pill).toBeVisible();

    const before = await activeStoreFromToken(page);
    expect(before, 'token should carry an active_store_id').toBeTruthy();

    // Pick a target store that differs from the current one.
    const target =
      before === SEED.primaryStore ? SEED.secondaryStore : SEED.primaryStore;

    await pill.click();
    const listbox = page.getByRole('listbox');
    await expect(listbox).toBeVisible();
    // Each option shows the store id (e.g. "BV-BOK-02") as its mono sub-line.
    await listbox.getByRole('button', { name: new RegExp(target) }).click();

    // #327: the token must be re-issued with the new active_store_id.
    await expect
      .poll(() => activeStoreFromToken(page), {
        message: 'JWT active_store_id did not change after store switch',
        timeout: 15_000,
      })
      .toBe(target);

    const after = await activeStoreFromToken(page);
    expect(after).not.toBe(before);
    expect(after).toBe(target);

    // Re-scoping: a fresh API client switched to the same store must report
    // the matching active_store_id and return store-scoped orders without error.
    const switched = await api.switchStore(target);
    expect(switched.activeStoreId).toBe(target);
    expect(api.activeStoreId).toBe(target);

    const orders = await api.getJson('/api/v1/orders');
    // /orders is store-scoped to the token's active store — every returned
    // order belongs to the switched-to store (empty list is also valid).
    const list = orders.orders ?? orders.data ?? [];
    for (const o of list) {
      expect(o.storeId ?? o.store_id).toBe(target);
    }
  });
});

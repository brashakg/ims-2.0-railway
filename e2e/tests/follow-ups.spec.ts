/**
 * Follow-ups endpoints + page (guards PR #332 — QA follow-ups 500s).
 *
 * /follow-ups/ and /follow-ups/summary used to 500 (a non-bool-safe DB
 * wrapper). #332 made them tolerant. This spec asserts both return 200 and
 * the dashboard page (/customers/follow-ups) actually renders its content
 * instead of getting stuck on a loading state.
 */
import { test, expect } from '../fixtures/test';
import { SEED } from '../fixtures/constants';

test.describe('Follow-ups', () => {
  test('/follow-ups/ and /follow-ups/summary return 200', async ({ api }) => {
    const store = SEED.primaryStore;

    const listRes = await api.rawGet(
      `/api/v1/follow-ups/?store_id=${encodeURIComponent(store)}`
    );
    expect(listRes.status(), await listRes.text()).toBe(200);
    const list = await listRes.json();
    // Endpoint returns an array of follow-ups (possibly empty) — not a 500.
    expect(Array.isArray(list)).toBe(true);

    const summaryRes = await api.rawGet(
      `/api/v1/follow-ups/summary?store_id=${encodeURIComponent(store)}`
    );
    expect(summaryRes.status(), await summaryRes.text()).toBe(200);
    const summary = await summaryRes.json();
    // Summary carries the numeric KPI fields the dashboard reads.
    expect(summary).toHaveProperty('due_today');
    expect(summary).toHaveProperty('overdue');
    expect(summary).toHaveProperty('pending_total');
    expect(typeof summary.due_today).toBe('number');
  });

  test('follow-ups dashboard renders (not stuck loading)', async ({ page }) => {
    await page.goto('/customers/follow-ups', { waitUntil: 'domcontentloaded' });

    // Editorial header proves the page rendered past any loading gate.
    await expect(
      page.getByRole('heading', { name: 'The nudge queue.' })
    ).toBeVisible();

    // The five summary cards are present.
    for (const label of ['Due Today', 'This Week', 'Overdue', 'Completed', 'Pending']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }

    // It must NOT be stuck on a loading placeholder.
    await expect(page.getByText(/Loading follow-ups/i)).toHaveCount(0);
  });
});

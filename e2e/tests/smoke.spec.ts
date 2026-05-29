/**
 * Smoke checks for screens that previously had dead-ends / 500s.
 *
 *  - Activity Log (/admin/activity-log) renders for SUPERADMIN (PR #325).
 *  - Tasks "New task" CTA opens a real create path, not a circular dead-end
 *    (PR #329 / QA F14): the modal links to the working form on
 *    /tasks/dashboard.
 *  - Notifications page renders.
 *
 * @smoke
 */
import { test, expect } from '../fixtures/test';

test.describe('Smoke @smoke', () => {
  test('Activity Log renders for SUPERADMIN', async ({ page }) => {
    await page.goto('/admin/activity-log', { waitUntil: 'domcontentloaded' });
    await expect(
      page.getByRole('heading', { name: 'User Activity Log' })
    ).toBeVisible();
    // The role-guard fallback must NOT be showing for the SUPERADMIN test user.
    await expect(page.getByText('Superadmin only')).toHaveCount(0);
  });

  test('Tasks "New task" opens a real create path (not a dead-end)', async ({
    page,
  }) => {
    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });

    // The board renders its priority view.
    await expect(
      page.getByRole('heading', { name: 'The shift, by priority.' })
    ).toBeVisible();

    await page.getByRole('button', { name: /New task/ }).click();

    // The modal acknowledges the action and routes to the WORKING form on the
    // Tasks Dashboard (F14: was href="/tasks" — this same page — a dead-end).
    await expect(
      page.getByRole('heading', { name: 'Create a task' })
    ).toBeVisible();
    const link = page.getByRole('link', { name: 'Open Tasks Dashboard' });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', '/tasks/dashboard');

    // Following it lands on the real dashboard create surface.
    await link.click();
    await expect(page).toHaveURL(/\/tasks\/dashboard/);
  });

  test('Notifications page renders', async ({ page }) => {
    await page.goto('/notifications', { waitUntil: 'domcontentloaded' });
    await expect(
      page.getByRole('heading', { name: 'Notifications' })
    ).toBeVisible();
  });
});

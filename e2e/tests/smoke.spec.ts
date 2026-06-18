/**
 * Smoke checks for screens that previously had dead-ends / 500s.
 *
 *  - Activity Log (/admin/activity-log) renders for SUPERADMIN (PR #325).
 *  - Tasks "New task" CTA opens a real create path, not a circular dead-end
 *    (QA F14 / owner #8). The earlier placeholder that merely linked to
 *    "Open Tasks Dashboard" was REPLACED by a full inline create form
 *    (components/tasks/NewTaskModal.tsx), so this asserts the live create
 *    surface (the "New task" dialog with a Title field + a Create-task
 *    button), not the retired link.
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

    // The "New task" dialog is a real inline create form (not the old
    // "Open Tasks Dashboard" link dead-end): it carries a Title field and a
    // Create-task button wired to tasksApi.createTask.
    const dialog = page.getByRole('dialog', { name: 'New task' });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByPlaceholder(/Reconcile Vision-Express/)
    ).toBeVisible();
    await expect(
      dialog.getByRole('button', { name: /Create task/ })
    ).toBeVisible();
  });

  test('Notifications page renders', async ({ page }) => {
    await page.goto('/notifications', { waitUntil: 'domcontentloaded' });
    await expect(
      page.getByRole('heading', { name: 'Notifications' })
    ).toBeVisible();
  });
});

/**
 * Layout probe — EVERY screen in the route inventory, at EVERY width in
 * VIEWPORTS, authenticated, in a real browser, measured.
 *
 * This is the regression test for BUG-1: the POS billing/delivery surfaces
 * were locked to viewport height with a fixed 430px right column at every
 * width, so on a 390px phone the two halves overlapped. It produced no
 * horizontal document overflow and Lighthouse scored the page fine.
 *
 * ONE spec, viewports looped inside it — deliberately NOT a second Playwright
 * project, which would re-run all five existing specs at phone width and
 * triple the lane for no signal.
 *
 * ONE TEST PER SCREEN, not per screen x viewport: navigation dominates the
 * cost (route chunk + data fetch), the measurement is microseconds, and
 * resizing re-lays-out the same page for free. 134 navigations instead of
 * 938 is the difference between a gate worth arming and one that gets
 * disabled for being slow. All 7 widths are still measured, and a failure
 * names every width that broke instead of only the first.
 *
 * The widths and the five rules live in ../fixtures/layout.ts; the screen
 * list lives in ../fixtures/routes.ts. Do not restate any of them here.
 */
import { test, expect } from '../fixtures/test';
import { VIEWPORTS, auditLayout } from '../fixtures/layout';
import { ROUTES, READY_DEFAULT } from '../fixtures/routes';

// Geometry is deterministic; a retry can only mask a real overlap.
test.describe.configure({ retries: 0 });

for (const screen of ROUTES) {
  test(`layout: ${screen.path}`, async ({ page }) => {
    const [first, ...rest] = VIEWPORTS;
    await page.setViewportSize({ width: first.width, height: first.height });
    await page.goto(screen.path, { waitUntil: 'domcontentloaded' });

    // A probe that silently scored the login page nine times is exactly the
    // failure mode being replaced here. Assert we are on the real screen...
    // Exact pathname, not a substring match: `/pos` must not be scored green
    // because the app bounced to `/pos/new`.
    await expect.poll(() => new URL(page.url()).pathname).toBe(screen.path);
    await page.locator(screen.ready ?? READY_DEFAULT).first().waitFor({ state: 'visible' });
    // ...and that the real screen is not the in-shell 404, whose <h1>404</h1>
    // satisfies the generic ready selector perfectly well.
    await expect(page.locator('#main-content h1', { hasText: /^404$/ })).toHaveCount(0);
    await page.waitForLoadState('networkidle').catch(() => {});

    const failures: string[] = [];
    for (const vp of [first, ...rest]) {
      if (vp !== first) {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        // Let responsive JS (width listeners, virtualised lists) settle.
        await page.waitForTimeout(150);
      }
      for (const v of await auditLayout(page)) failures.push(`  ${vp.name} [${v.rule}] ${v.detail}`);
    }

    expect(failures, `${screen.path}:\n${failures.join('\n')}`).toEqual([]);
  });
}

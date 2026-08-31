/**
 * Layout probe — every screen in SCREENS, at every width in VIEWPORTS,
 * authenticated, in a real browser, measured.
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
 * The widths, the screen list and the four rules all live in
 * ../fixtures/layout.ts. Do not restate any of them here.
 */
import { test, expect } from '../fixtures/test';
import { SCREENS, VIEWPORTS, auditLayout } from '../fixtures/layout';

// Geometry is deterministic; a retry can only mask a real overlap.
test.describe.configure({ retries: 0 });

for (const screen of SCREENS) {
  for (const vp of VIEWPORTS) {
    test(`layout: ${screen.path} @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(screen.path, { waitUntil: 'domcontentloaded' });

      // A probe that silently scored the login page nine times is exactly the
      // failure mode being replaced here. Assert we are on the real screen.
      await expect(page).toHaveURL(new RegExp(`${screen.path}(\\?|#|$)`));
      await page.locator(screen.ready).first().waitFor({ state: 'visible' });
      await page.waitForLoadState('networkidle').catch(() => {});

      const violations = await auditLayout(page);
      expect(
        violations,
        `${screen.path} at ${vp.name}:\n` +
          violations.map((v) => `  [${v.rule}] ${v.detail}`).join('\n'),
      ).toEqual([]);
    });
  }
}

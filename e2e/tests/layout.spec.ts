/**
 * Layout probe — every screen in ROUTES, at every width in VIEWPORTS,
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
 * ONE DOCUMENT LOAD PER SCREEN, ONE COMPONENT MOUNT PER WIDTH
 * ----------------------------------------------------------
 * Measured on this app: loading a screen costs ~1.5s, measuring it costs 3ms.
 * A test per screen-x-width therefore pays ~1.5s seven times for the same
 * 21ms of signal, which is what made the full matrix too slow to be a
 * required check.
 *
 * But a BARE resize is not equivalent to a fresh load: components that read
 * `window.innerWidth` while rendering and register no resize listener keep
 * their stale desktop/mobile state (DisplayLayoutPanel.tsx is one). So each
 * width gets a real MOUNT: route away to a path nothing matches, wait for the
 * old subtree to actually leave the DOM, and route back. That is a
 * client-side navigation (~250ms), not a document load (~1500ms), so every
 * width is measured on a component that mounted at that width for a fifth of
 * the cost.
 *
 * Known ceiling: the app SHELL and its providers stay mounted across the
 * whole loop, so a width read once at module scope or in a provider would not
 * be re-evaluated. The audit root is `#main-content` (inside the shell) and
 * the shell's own phone rules are covered by
 * frontend/src/styles/__tests__/shellPhoneLayout.test.ts, so nothing in scope
 * is missed today. If that ever changes, reload per width instead.
 *
 * The widths, the screen list and the five rules all live in
 * ../fixtures/layout.ts. Do not restate any of them here.
 */
import { test, expect } from '../fixtures/test';
import { AUDIT_ROOT, VIEWPORTS, auditLayout } from '../fixtures/layout';
// The route list lives in routes.ts, NOT here: it is the half that carries
// the coverage guard (a new screen that is neither probed nor excluded with
// a reason fails routes-inventory.spec.ts by name).
import { ROUTES, READY_DEFAULT, knownBreak } from '../fixtures/routes';

// Geometry is deterministic; a retry can only mask a real overlap.
test.describe.configure({ retries: 0 });

/** A path no route claims, so routing to it unmounts whatever is on screen. */
const NOWHERE = '/__layout-probe-nowhere__';
/** No DOM change under the audited root for this long = the render is done. */
const QUIET_MS = 400;
/** Upper bound on that wait, so a never-quiet screen cannot hang the run. */
const CAP_MS = 6_000;

for (const screen of ROUTES) {
  test(`layout: ${screen.path}`, async ({ page }) => {
    const go = (to: string) =>
      page.evaluate((t: string) => {
        history.pushState({}, '', t);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, to);

    /**
     * Wait until the screen has actually finished rendering.
     *
     * `waitForLoadState('networkidle')` is a DOCUMENT-level signal. After a
     * client-side navigation there is no new document load, so it resolves
     * immediately and happily lets us measure a half-drawn screen. Measured on
     * /cash-flow at 360px: `AUDIT_ROOT` held 22 elements at that moment and 102
     * once the data arrived -- and the 5px overflow that only exists in the
     * full render was scored as PASS. A probe that measures a skeleton is
     * worse than no probe.
     *
     * So settle on the DOM itself: no element added or removed under the
     * audited root for QUIET_MS. That covers the fetch AND the render it
     * triggers, and it needs to know nothing about the app's data layer.
     * `networkidle` is kept as well because on the one real document load it
     * is free and genuinely informative.
     */
    const settle = async () => {
      await page.locator(screen.ready ?? READY_DEFAULT).first().waitFor({ state: 'visible' });
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.evaluate(() => delete (window as unknown as Record<string, unknown>).__probeSettle);
      await page.waitForFunction(
        ([root, quietMs, capMs]) => {
          const w = window as unknown as Record<string, unknown>;
          const el = document.querySelector(root as string);
          if (!el) return false;
          const n = el.querySelectorAll('*').length;
          const now = performance.now();
          const s = (w.__probeSettle ??= { n: -1, since: now, start: now }) as {
            n: number; since: number; start: number;
          };
          if (n !== s.n) {
            s.n = n;
            s.since = now;
          }
          // ponytail: the cap keeps a screen that never stops mutating (a live
          // clock, a spinner that swaps nodes) from hanging the run; it is
          // measured anyway. If a screen ever needs longer, raise CAP_MS.
          return now - s.since >= (quietMs as number) || now - s.start >= (capMs as number);
        },
        [AUDIT_ROOT, QUIET_MS, CAP_MS] as const,
        { polling: 100 },
      );
    };

    await page.setViewportSize({ width: VIEWPORTS[0].width, height: VIEWPORTS[0].height });
    await page.goto(screen.path, { waitUntil: 'domcontentloaded' });

    // A probe that silently scored the login page nine times is exactly the
    // failure mode being replaced here. Assert we are on the real screen.
    // A URL predicate, not a RegExp built by string concatenation: a screen
    // path is a literal, and escaping one into a pattern is a bug factory.
    await page.waitForURL((u) => u.pathname === screen.path);
    await settle();

    // Every width is measured even after one of them fails, so a broken screen
    // reports every width it is broken at in one run instead of one per run.
    const failures: string[] = [];
    // Quarantined breaks that have started passing - the list must shrink.
    const fixed: string[] = [];
    for (const vp of VIEWPORTS) {
      await test.step(`@ ${vp.name}`, async () => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        // Tag the live subtree, route to nowhere, and do not continue until
        // that tag is gone -- proof the screen really unmounted, rather than a
        // hopeful sleep.
        const tagged = await page.evaluate((root) => {
          const el = document.querySelector(root)?.firstElementChild;
          if (!el) return false;
          el.setAttribute('data-probe-old', '1');
          return true;
        }, AUDIT_ROOT);
        // Without a tag the detach wait below would pass vacuously and the
        // remount would go unverified. Fail loudly instead.
        expect(tagged, `${screen.path}: nothing inside ${AUDIT_ROOT} to unmount`).toBe(true);
        await go(NOWHERE);
        await page.locator('[data-probe-old]').waitFor({ state: 'detached' });
        await go(screen.path);
        await settle();

        const violations = await auditLayout(page);
        // A screen already broken when the gate was built is QUARANTINED, not
        // waved through: only its ONE recorded (rule, width) pair is filtered
        // out, so a new break - or the same break spreading to another width -
        // is still red.
        const known = knownBreak(screen.path, vp.width);
        for (const v of violations) {
          if (known && v.rule === known.rule) continue;
          failures.push(`  at ${vp.width}px wide (${vp.name}) -- [${v.rule}] ${v.detail}`);
        }
        // The list can only shrink. When a recorded break stops reporting, say
        // so loudly rather than leaving a stale exemption behind to hide the
        // next regression on that screen.
        if (known && !violations.some((v) => v.rule === known.rule)) {
          fixed.push(
            `  ${screen.path} at ${vp.width}px no longer reports [${known.rule}] -- ` +
              `delete that entry from KNOWN_BROKEN in fixtures/routes.ts`,
          );
        }
      });
    }

    expect(failures, `Broken layout on ${screen.path}:\n${failures.join('\n')}\n`).toEqual([]);
    expect(
      fixed,
      'Good news - a quarantined break is fixed:\n' + fixed.join('\n'),
    ).toEqual([]);
  });
}

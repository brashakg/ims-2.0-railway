/**
 * Popup layout probe — every popup in POPUPS, at every width in VIEWPORTS,
 * authenticated, in a real browser, measured.
 *
 * Why a second spec: until 4afe9d8 modals were INVISIBLE to the probe. Every
 * control inside a dialog inherited `position: fixed` from the dialog itself,
 * `isFloating` walked all the way to <html>, and so every control was skipped
 * — a dialog scored zero controls and passed. `auditLayout(page, {
 * rootSelector })` now stops that walk at the audit root, so pointing it at an
 * OPEN dialog measures the dialog. Nothing opened one. This does.
 *
 * The widths and the five rules live in ../fixtures/layout.ts and are IMPORTED,
 * never restated. This file owns exactly one new thing: the list of popups and
 * how to open each of them.
 *
 * Determinism over breadth. A blocking gate that flakes gets switched off
 * within a week, so every entry here was hand-checked against a running stack;
 * nothing is auto-discovered at test time and nothing destructive is ever
 * clicked. Everything NOT covered is in POPUP_EXCLUSIONS, and everything
 * covered-but-currently-broken is in KNOWN_BROKEN — both with reasons, both
 * guarded, because a gate that quietly skips reads as "all green".
 *
 * Shape: one test per popup with the widths looped INSIDE it. Seven separate
 * tests per popup meant seven browser contexts and seven cold navigations for
 * the same modal; this way the whole file is ~19 contexts. All seven widths are
 * still measured and all failures are reported together, not fail-fast.
 */
import { test, expect } from '../fixtures/test';
import { VIEWPORTS, auditLayout, type Violation } from '../fixtures/layout';
import { SEED } from '../fixtures/constants';

// Geometry is deterministic; a retry can only mask a real overlap.
test.describe.configure({ retries: 0 });

/**
 * What an OPEN popup looks like in this app. There is no dialog library —
 * two hand-rolled conventions exist and this selector covers both:
 *
 *   1. `role="dialog"` on the panel (13 components; incl. the cmdk palette and
 *      the tasks `.nt-modal`, whose overlay is plain CSS with no utility class).
 *   2. the dominant one: a Tailwind backdrop `div.fixed.inset-0 …` wrapping the
 *      panel (~100 call sites, z-40/50/60/70/80/100, centred OR `justify-end`
 *      for right-side drawers). No role, no aria-modal.
 *
 * Where a component uses both they are the SAME element, so the union never
 * double-counts. Each test asserts exactly one match while the popup is open —
 * that is what makes `document.querySelector(POPUP)` unambiguous inside
 * auditLayout, instead of silently auditing a backdrop.
 */
const POPUP = '[role="dialog"], div.fixed.inset-0';

type Popup = {
  /** Route the trigger lives on. */
  path: string;
  /** Accessible name of the button that opens it (never destructive). */
  trigger: string;
  /** Test name, and the key KNOWN_BROKEN refers to. */
  name: string;
  /**
   * Widths at which the trigger is NOT RENDERED AT ALL (a responsive hide, not
   * a layout fault). Asserted as an absence, never skipped: if the button ever
   * appears at that width the assertion fails and whoever made it appear gains
   * the coverage by deleting this field.
   */
  absentBelow?: { width: number; why: string };
};

/**
 * Hand-checked popups. Ordered highest-value first: POS is revenue-critical,
 * and HR/payroll is where the owner reported a modal clipped on a phone.
 *
 * Openers are read-only by construction — the discovery pass excluded every
 * name matching delete/remove/void/refund/approve/pay/cancel/submit/confirm/
 * save/send/… — so a test that opens one and never submits mutates nothing.
 */
const POPUPS: ReadonlyArray<Popup> = [
  // ── POS (revenue-critical) ───────────────────────────────────────────────
  {
    path: '/pos',
    trigger: 'Recall',
    name: 'pos held-bills',
    absentBelow: {
      width: 768,
      why: 'Below 768px `.steps-rail` computes display:none, so Hold/Recall/New/Walkout do not exist on a phone at all. index.css:2606 claims the opposite ("Keep Hold / Recall / New / +walk-in / Walkout reachable on phones"), so this absence is a POS phone regression in its own right — reported separately. It is not a popup-geometry fault, so it is asserted here rather than measured.',
    },
  },
  {
    path: '/pos',
    trigger: 'Walkout',
    name: 'pos walkout-intake',
    absentBelow: {
      width: 768,
      why: 'Same `.steps-rail { display: none }` phone rule as "pos held-bills" above.',
    },
  },
  { path: '/pos', trigger: 'Create new customer', name: 'pos add-customer' },
  // ── HR / payroll (owner-reported phone clipping) ─────────────────────────
  { path: '/hr/salary-setup', trigger: '+ Add salary', name: 'hr salary-config' },
  // ── Clinical ─────────────────────────────────────────────────────────────
  { path: '/clinical', trigger: 'New patient', name: 'clinical patient-intake' },
  { path: '/clinical', trigger: 'Queue existing', name: 'clinical queue-existing' },
  // ── Inventory ────────────────────────────────────────────────────────────
  { path: '/inventory', trigger: 'Manage Barcode', name: 'inventory barcode' },
  { path: '/inventory', trigger: 'View Details', name: 'inventory stock-detail' },
  { path: '/inventory/audit', trigger: 'New stock count', name: 'inventory new-count' },
  // ── Purchase ─────────────────────────────────────────────────────────────
  { path: '/purchase', trigger: 'New PO', name: 'purchase new-po' },
  { path: '/purchase/suppliers', trigger: 'New supplier', name: 'purchase new-supplier' },
  // ── Catalog (role="dialog" convention) ───────────────────────────────────
  { path: '/catalog', trigger: SEED.frame.name, name: 'catalog product-drawer' },
  // ── Tasks (.nt-modal convention — no Tailwind backdrop) ──────────────────
  { path: '/tasks', trigger: 'New task', name: 'tasks new-task' },
  { path: '/tasks/checklists', trigger: 'Create Task', name: 'tasks checklist-task' },
  // ── Finance / operations ─────────────────────────────────────────────────
  { path: '/finance/expenses', trigger: 'Add expense', name: 'finance add-expense' },
  { path: '/workshop', trigger: 'New job from order', name: 'workshop new-job' },
  { path: '/promotions', trigger: 'New rule', name: 'promotions new-rule' },
  { path: '/estimates', trigger: 'Create Estimate', name: 'estimates create' },
  // ── Right-side drawer geometry (justify-end, not centred) ────────────────
  {
    path: '/online-store/collections',
    trigger: 'New smart',
    name: 'online-store new-smart-collection',
  },
];

/**
 * Popups this probe MEASURED and found genuinely broken on unmodified main.
 * Every one was confirmed against the failure screenshot — none is a probe
 * artefact. They are quarantined so the gate can start blocking REGRESSIONS
 * today instead of waiting on a responsive-fix PR.
 *
 * The assertion is INVERTED for these: the test fails if a listed popup starts
 * measuring clean. That is what stops the list rotting — you cannot fix one of
 * these and forget to re-arm it.
 */
const KNOWN_BROKEN: ReadonlyArray<{ name: string; widths: number[]; why: string }> = [
  {
    name: 'pos add-customer',
    widths: [390, 430],
    why: 'The overlay is `fixed inset-0` but its containing block is `.pos-work` (689px tall at 390x844), not the viewport, while the panel is sized `max-h-[90dvh]` = 760px. Result: the modal header (with Close) and the Cancel / Create Customer row are cut off and unreachable. Screenshot-confirmed.',
  },
  {
    name: 'hr salary-config',
    widths: [768, 820],
    why: 'THE OWNER-REPORTED ONE. The salary-config modal is taller than the viewport with no scroller, so entity/store/name rows land at y=-71..33 — above the top edge, unreachable. Screenshot-confirmed at 768x1024 and 820x1180.',
  },
  {
    name: 'tasks new-task',
    widths: [360, 390, 430, 768, 820],
    why: '`.nt-modal` is `width: 1060px; max-width: 100%` inside `.nt-overlay { display: grid; place-items: center }`. The implicit grid track sizes to max-content, so `max-width: 100%` resolves against 1060px and never clamps: the modal renders 1060px wide on a 360px phone with both edges cut and no horizontal scroll. Screenshot-confirmed. Worst of the five.',
  },
  {
    name: 'estimates create',
    widths: [360, 390, 430, 768, 820, 1180, 1440],
    why: 'Broken at EVERY width: the panel clips its own first row, so Customer name / phone / email sit at y=67..103 behind the panel edge, unreachable. At 360px the Offer Price field additionally overlaps the delete-line button by 9x32px. Screenshot-confirmed at 1180x820 and 360x780.',
  },
  {
    name: 'online-store new-smart-collection',
    widths: [360],
    why: 'At 360px the Smart-rules section collides with the drawer footer: the description textarea overlaps the "ALL rules (AND)" / "ANY rule (OR)" toggles by ~9px of their height, so part of each toggle is not clickable. Screenshot-confirmed.',
  },
];

/**
 * NOT covered, and why. A gate that quietly skips reads as "all green", which
 * is worse than no gate — so every gap is written down here and the guard test
 * below fails if an entry is ever added without a real reason.
 */
const POPUP_EXCLUSIONS: ReadonlyArray<{ what: string; why: string }> = [
  {
    what: 'POS Wave-4 surfaces /pos/new and /pos/delivery',
    why: 'They render no popup at all today: frontend/src/pages/pos/next/{BillingSurface,DeliverySurface,PosWidgets}.tsx contain zero role="dialog" and zero fixed inset-0. Their page layout is already covered by tests/layout.spec.ts.',
  },
  {
    what: 'POS in-cart modals (DiscountModal, PrescriptionSelectModal, LensDetailsModal, LensFittingFormModal, GST Tax Invoice)',
    why: 'Each needs a salesperson + customer + a cart line before its button exists. That setup is the slowest and flakiest path in the suite and it writes orders; a blocking layout gate must not depend on it. Cover these once a non-mutating cart fixture exists.',
  },
  {
    what: 'Any opener whose accessible name matches delete/remove/void/refund/approve/reject/pay/cancel/submit/confirm/save/send/lock/export/print/transfer/write-off/...',
    why: 'Never auto-clicked, at discovery time or at test time. A layout probe must not be able to void a bill or approve a refund in order to measure a box.',
  },
  {
    what: 'App-shell popups: mobile nav drawer (#rail-drawer), notification panel, store-switch confirm, cmdk command palette',
    why: 'fixtures/layout.ts deliberately scopes to #main-content because the shell has its own guard at frontend/src/styles/__tests__/shellPhoneLayout.test.ts. Duplicating it here would be one rule with two implementations, this repo\'s dominant defect class.',
  },
  {
    what: 'Modals reachable only from a row the E2E seed does not create (returns/approvals RefundApprovalModal, approvals PINApproveModal, handoff modals, POLifecycleDrawer, SuperadminOrderEditModal, serial/reorder/display-fixture modals)',
    why: 'Their trigger is simply not on the page against seed_e2e.py data, so a test would silently pass on an empty list. They need seed rows first: extend backend/scripts/seed_e2e.py, then move the entry up into POPUPS.',
  },
  {
    what: 'Second triggers for a modal already covered (/customers "New customer", /walkouts "Log Walkout")',
    why: 'They mount the SAME component as /pos "Create new customer" and /pos "Walkout" respectively. Seven more viewport runs each, for zero new geometry.',
  },
];

test('popup coverage list declares every gap with a reason', () => {
  // A silent cap is the failure mode being prevented: entries may be added to
  // these lists, but not without saying what and why.
  for (const e of POPUP_EXCLUSIONS) {
    expect(
      e.what.trim().length,
      `exclusion missing "what": ${JSON.stringify(e)}`,
    ).toBeGreaterThan(10);
    expect(
      e.why.trim().length,
      `exclusion "${e.what}" needs a real reason, not a placeholder`,
    ).toBeGreaterThan(60);
  }
  const names = new Set(POPUPS.map((p) => p.name));
  for (const k of KNOWN_BROKEN) {
    expect(names, `KNOWN_BROKEN names a popup that is not covered: ${k.name}`).toContain(k.name);
    expect(k.why.trim().length, `KNOWN_BROKEN "${k.name}" needs a real reason`).toBeGreaterThan(60);
    expect(k.widths.length, `KNOWN_BROKEN "${k.name}" must name the widths`).toBeGreaterThan(0);
    for (const w of k.widths) {
      expect(
        VIEWPORTS.map((v) => v.width),
        `KNOWN_BROKEN "${k.name}" quarantines width ${w}, which is not a probed viewport`,
      ).toContain(w);
    }
  }
  for (const p of POPUPS) {
    if (!p.absentBelow) continue;
    expect(
      p.absentBelow.why.trim().length,
      `"${p.name}" absentBelow needs a real reason`,
    ).toBeGreaterThan(60);
  }
  // Coverage floor — deleting entries to make the gate green must break it.
  expect(POPUPS.length, 'popup coverage shrank').toBeGreaterThanOrEqual(19);
});

for (const p of POPUPS) {
  test(`popup: ${p.name}`, async ({ page }) => {
    // Seven widths in one test: ~5s of navigation each, well inside this.
    test.setTimeout(180_000);
    const failures: string[] = [];

    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(p.path, { waitUntil: 'domcontentloaded' });
      // Same guard as layout.spec: never score a redirect (login/select-store).
      await expect(page).toHaveURL(new RegExp(`${p.path}(\\?|#|$)`));

      const trigger = page
        .locator('#main-content')
        .getByRole('button', { name: p.trigger })
        .first();

      if (p.absentBelow && vp.width < p.absentBelow.width) {
        // Documented absence, asserted rather than skipped.
        await expect(
          trigger,
          `${p.name} @ ${vp.name}: "${p.trigger}" is documented as not rendered ` +
            `below ${p.absentBelow.width}px, but it is here now. Delete absentBelow ` +
            `and take the coverage. Reason on record: ${p.absentBelow.why}`,
        ).toHaveCount(0);
        continue;
      }

      // The trigger becoming visible IS the route-ready signal.
      await trigger.waitFor({ state: 'visible' });
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click();

      const popup = page.locator(POPUP);
      await popup.first().waitFor({ state: 'visible' });
      // Exactly one match, or `document.querySelector(POPUP)` inside
      // auditLayout would be measuring something other than this popup.
      await expect(popup, `${p.name} @ ${vp.name}: expected exactly one open popup`).toHaveCount(1);
      // Entry animations (nt-pop is 220ms) settle before geometry is read.
      await page.waitForTimeout(400);

      const violations: Violation[] = await auditLayout(page, { rootSelector: POPUP });
      // No close step: the next iteration navigates away, which unmounts the
      // popup. Clicking a close control would only add a flake source.

      const quarantined = KNOWN_BROKEN.find(
        (k) => k.name === p.name && k.widths.includes(vp.width),
      );
      if (quarantined) {
        if (violations.length === 0) {
          failures.push(
            `${vp.name}: KNOWN_BROKEN but now measures CLEAN — delete width ${vp.width} ` +
              `from the "${p.name}" entry so this width starts blocking. On record: ${quarantined.why}`,
          );
        }
        continue;
      }
      if (violations.length) {
        failures.push(
          `${vp.name}:\n` + violations.map((v) => `    [${v.rule}] ${v.detail}`).join('\n'),
        );
      }
    }

    expect(failures, `${p.name} (${p.path})\n  ` + failures.join('\n  ')).toEqual([]);
  });
}

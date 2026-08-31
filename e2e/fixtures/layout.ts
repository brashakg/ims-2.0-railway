/**
 * Layout probe — the ONE place the viewport list, the screen list and the
 * "is this screen broken" rules live.
 *
 * Why it exists: BUG-1 (POS surfaces locked to viewport height with a fixed
 * 430px right column at every width) shipped past a green suite because
 * nothing in this repo ever drove a browser at more than one width, and
 * because the breakage produced ZERO horizontal document overflow — the
 * container was height-locked, so the two halves overlapped INSIDE it.
 * A "does the page scroll sideways" check cannot see that. Geometry can.
 *
 * One rule, one implementation: `auditLayout` is the only implementation of
 * these four checks. `tests/layout.spec.ts` calls it; anything else that ever
 * needs it (PIXEL reads the CI result — it does not get a browser) imports
 * the same function. Do not re-write the width list or the overlap rule.
 *
 * Deliberately NOT here: screenshot/pixel diffing. It needs committed
 * baselines and goes flaky on CI font rendering; geometry is deterministic.
 */
import type { Page } from '@playwright/test';

/** Widths every probed screen must survive. iPad landscape is the owner's
 *  POS register target (spec 11b); the phone is what BUG-1 broke. */
export const VIEWPORTS = [
  // Smallest phone still in real use - the width most layouts break at first.
  { name: 'phone-small 360x780', width: 360, height: 780 },
  { name: 'phone 390x844', width: 390, height: 844 },
  { name: 'phone-large 430x932', width: 430, height: 932 },
  // 768 is the Tailwind md/lg hinge - the width where a two-column layout
  // flips. BUG-1 lived exactly on such a hinge.
  { name: 'tablet-small 768x1024', width: 768, height: 1024 },
  { name: 'ipad-portrait 820x1180', width: 820, height: 1180 },
  // The owner's POS register target (spec 11b).
  { name: 'ipad-landscape 1180x820', width: 1180, height: 820 },
  { name: 'laptop 1440x900', width: 1440, height: 900 },
] as const;

/** Screens under probe. Adding one is a single line.
 *  `ready` is a selector that only exists once the lazy route has painted. */
/** The audited subtree: the page body, not the app shell. Exported so the
 *  spec's render-settled wait watches exactly what the audit measures. */
export const AUDIT_ROOT = '#main-content';

/** Default `ready` for a screen: something a painted page has and a blank
 *  one does not. Screens override it only when this is not enough. */
export const READY_DEFAULT = `${AUDIT_ROOT} :is(h1, h2, table, button)`;

/**
 * The screens the gate measures. A spread, not the whole app: one screen costs
 * ~14s (one load + a remount at each of the seven widths), and at --workers=4
 * that is ~4s of wall clock each. All 103 reachable parameterless routes were
 * measured once at this size and cost 412s -- too much for a required check,
 * so this list is sized to the budget and picked to cover one of each layout
 * archetype instead.
 *
 * That full sweep also found 15 screens genuinely broken at 360px (all
 * body-hscroll, mostly wide tables): /customers/whatsapp-inbox,
 * /finance/budgeting, /finance/cash-flow, /incentive (+ /leaderboard, /payout,
 * /payouts, /settings), /inventory/power-grid, /inventory/replenishment,
 * /pos/footfall, /promotions, /reports/promotions, /walkouts (+ /dashboard).
 * They are NOT listed here because they would make the gate red on arrival --
 * add each one as it is fixed. `/pos` also never satisfied `READY_DEFAULT`
 * within 15s and needs its own `ready` before it can be listed.
 */
export const SCREENS: ReadonlyArray<{ path: string; ready?: string }> = [
  // the BUG-1 surfaces - the regression this probe exists for
  { path: '/pos/new' },
  { path: '/pos/delivery' },
  // the screens a store opens every day
  { path: '/dashboard' },
  { path: '/orders' },
  { path: '/estimates' },
  { path: '/tasks' },
  { path: '/my-work' },
  { path: '/approvals' },
  // money
  { path: '/finance/dashboard' },
  { path: '/finance/cash-register' },
  { path: '/finance/blind-eod' },
  { path: '/reports/day-end' },
  // stock and buying
  { path: '/inventory' },
  { path: '/inventory/audit' },
  { path: '/purchase/grn' },
  { path: '/purchase/receive' },
  { path: '/catalog' },
  { path: '/catalog/add' },
  // customer-facing records
  { path: '/customers' },
  { path: '/customers/360' },
  { path: '/prescriptions' },
  { path: '/clinical' },
  { path: '/returns' },
  // the long-form admin screens, where wide tables live
  { path: '/hr/payroll' },
  { path: '/online-store/orders' },
];

export type Violation = {
  rule: 'doc-overflow' | 'body-hscroll' | 'overlap' | 'past-right-edge' | 'unreachable';
  detail: string;
};

/**
 * Measure the CURRENTLY LOADED page at its current viewport and report every
 * broken-layout violation. Runs entirely in the page (one round trip).
 *
 * Scope: the page body (`#main-content`), not the app shell — the shell's own
 * phone rules are already guarded by
 * frontend/src/styles/__tests__/shellPhoneLayout.test.ts and duplicating them
 * here would be one rule with two implementations. Document overflow is the
 * one global check.
 */
export async function auditLayout(
  page: Page,
  opts: { rootSelector?: string } = {},
): Promise<Violation[]> {
  return page.evaluate((rootSelector: string) => {
    const out: { rule: Violation['rule']; detail: string }[] = [];
    const de = document.documentElement;
    const vw = de.clientWidth;
    const vh = de.clientHeight;

    // ── 1. horizontal document overflow ──────────────────────────────────
    if (de.scrollWidth > de.clientWidth + 1) {
      out.push({
        rule: 'doc-overflow',
        detail: `document scrollWidth ${de.scrollWidth} > clientWidth ${de.clientWidth}`,
      });
    }

    const root = document.querySelector(rootSelector) ?? document.body;

    // Interactive controls only. Widening this to every text node multiplies
    // false positives for no extra signal: a broken column takes its controls
    // with it, and the controls are what the user has to be able to hit.
    const SELECTOR = [
      'button',
      'a[href]',
      'input:not([type="hidden"])',
      'select',
      'textarea',
      '[role="button"]',
      '[role="checkbox"]',
      '[role="tab"]',
      '[role="menuitem"]',
    ].join(',');

    const describe = (el: Element) => {
      const label =
        (el.getAttribute('aria-label') ||
          el.getAttribute('placeholder') ||
          el.textContent ||
          '')
          .trim()
          .replace(/\s+/g, ' ')
          .slice(0, 40);
      const cls =
        typeof el.className === 'string' && el.className.trim()
          ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
          : '';
      return `${el.tagName.toLowerCase()}${cls}${label ? ` "${label}"` : ''}`;
    };

    /** display/visibility/opacity hidden anywhere up the tree. */
    const styledVisible = (el: Element) => {
      let n: Element | null = el;
      while (n && n !== de) {
        const cs = getComputedStyle(n);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0)
          return false;
        n = n.parentElement;
      }
      return true;
    };

    /** Out-of-flow elements (menus, modals, popovers, sticky bars, icons
     *  absolutely placed inside a field) are SUPPOSED to sit on top of other
     *  things. Excluding them is what keeps the false-positive rate at zero
     *  on a healthy page. */
    const isFloating = (el: Element) => {
      let n: Element | null = el;
      // Stop AT the audit root. Walking to <html> meant that when the root is a
      // dialog (which is itself fixed), every control inside it inherited
      // "floating" and was skipped -- popups were invisible to this probe.
      while (n && n !== root && n !== de) {
        const p = getComputedStyle(n).position;
        if (p === 'fixed' || p === 'sticky' || p === 'absolute') return true;
        n = n.parentElement;
      }
      return false;
    };

    type Box = { left: number; top: number; right: number; bottom: number };

    /**
     * Clip an element's rect by every overflow-clipping ancestor and by the
     * viewport, and note whether anything could actually SCROLL to reveal it.
     * `overflow: hidden` is a clip the user cannot undo — that distinction is
     * exactly BUG-1's "action button below the fold of a height-locked box".
     */
    const measure = (el: Element) => {
      const raw = el.getBoundingClientRect();
      const r: Box = { left: raw.left, top: raw.top, right: raw.right, bottom: raw.bottom };
      let reachable = false; // some ancestor can be scrolled to reveal it
      let inHScroller = false; // legitimately scrolls sideways (wide table)
      let n: Element | null = el.parentElement;
      while (n) {
        const cs = getComputedStyle(n);
        const clipsX = cs.overflowX !== 'visible';
        const clipsY = cs.overflowY !== 'visible';
        if (clipsX || clipsY) {
          const b = n.getBoundingClientRect();
          if (clipsX) {
            r.left = Math.max(r.left, b.left);
            r.right = Math.min(r.right, b.right);
          }
          if (clipsY) {
            r.top = Math.max(r.top, b.top);
            r.bottom = Math.min(r.bottom, b.bottom);
          }
          const scrollsX =
            (cs.overflowX === 'auto' || cs.overflowX === 'scroll') &&
            n.scrollWidth > n.clientWidth + 1;
          const scrollsY =
            (cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
            n.scrollHeight > n.clientHeight + 1;
          if (scrollsX || scrollsY) reachable = true;
          // Only a scroller strictly BELOW the audit root counts as deliberate.
          if (scrollsX && n !== root && root.contains(n)) inHScroller = true;
        }
        n = n.parentElement;
      }
      if (de.scrollHeight > de.clientHeight + 1 || de.scrollWidth > de.clientWidth + 1)
        reachable = true;
      const vis: Box = {
        left: Math.max(r.left, 0),
        top: Math.max(r.top, 0),
        right: Math.min(r.right, vw),
        bottom: Math.min(r.bottom, vh),
      };
      const gone = vis.right - vis.left <= 1 || vis.bottom - vis.top <= 1;
      return { raw, vis, gone, reachable, inHScroller };
    };

    // ── 1b. the page body itself scrolls sideways ────────────────────────
    // Wide content belongs in its OWN overflow-x container; the body scrolling
    // horizontally means something simply does not fit the screen.
    if (root instanceof HTMLElement && root.scrollWidth > root.clientWidth + 1) {
      const widest = Array.from(root.querySelectorAll('*'))
        .map((el) => ({ el, r: el.getBoundingClientRect() }))
        .filter((x) => x.r.width > 2 && x.r.right > root.getBoundingClientRect().right + 1)
        .sort((a, b) => b.r.right - a.r.right)[0];
      out.push({
        rule: 'body-hscroll',
        detail:
          `page body scrolls sideways: scrollWidth ${root.scrollWidth} > ` +
          `clientWidth ${root.clientWidth}` +
          (widest ? ` -- widest offender ${describe(widest.el)}` : ''),
      });
    }

    const nodes: { el: Element; m: ReturnType<typeof measure> }[] = [];
    for (const el of Array.from(root.querySelectorAll(SELECTOR))) {
      if (!styledVisible(el) || isFloating(el)) continue;
      const raw = el.getBoundingClientRect();
      if (raw.width < 2 || raw.height < 2) continue; // sr-only, collapsed
      const m = measure(el);

      // ── 3. clipped away with no way to scroll to it ────────────────────
      if (m.gone && !m.reachable) {
        out.push({
          rule: 'unreachable',
          detail: `${describe(el)} at y=${Math.round(raw.top)}..${Math.round(
            raw.bottom,
          )} x=${Math.round(raw.left)}..${Math.round(
            raw.right,
          )} is clipped out of a ${vw}x${vh} viewport and nothing can scroll to it`,
        });
        continue;
      }
      if (m.gone) continue; // off-screen but scrollable to: fine, and not comparable

      // ── 4. sticking out past the right edge ────────────────────────────
      if (raw.right > vw + 1 && !m.inHScroller) {
        out.push({
          rule: 'past-right-edge',
          detail: `${describe(el)} right edge ${Math.round(raw.right)} > viewport width ${vw}`,
        });
      }
      nodes.push({ el, m });
    }

    // ── 2. overlapping controls ──────────────────────────────────────────
    // O(n^2) over the visible controls of one screen (low hundreds). If a
    // screen ever gets big enough for this to matter, sort by top edge and
    // sweep — not before.
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue; // nesting is not overlap
        const ow = Math.min(a.m.vis.right, b.m.vis.right) - Math.max(a.m.vis.left, b.m.vis.left);
        const oh = Math.min(a.m.vis.bottom, b.m.vis.bottom) - Math.max(a.m.vis.top, b.m.vis.top);
        // 2px slack absorbs shared/negative-margin borders and sub-pixel rounding.
        if (ow <= 2 || oh <= 2) continue;
        out.push({
          rule: 'overlap',
          detail: `${describe(a.el)} overlaps ${describe(b.el)} by ${Math.round(ow)}x${Math.round(oh)}px`,
        });
      }
    }
    return out;
  }, opts.rootSelector ?? AUDIT_ROOT);
}

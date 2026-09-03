/**
 * Route inventory — the ONE list of app screens the layout gate probes.
 *
 * Why a committed file and not a test-time TSX parse: the probe should fail
 * because a SCREEN is broken, not because a JSX parser hiccuped. The parser
 * lives here too (`deriveRoutePaths`) but only the GUARD test runs it, to
 * prove this list is still complete — the same convention the backend uses
 * (a new route without an `rbac_policy` row fails CI).
 *
 * Regenerating after adding a screen is manual and deliberate: the guard
 * names the missing path and tells you which list to add it to.
 *
 * NO SILENT SKIPS. Every route in frontend/src/routes/*.tsx is either in
 * ROUTES (probed) or in EXCLUSIONS (with a reason). The guard fails if a
 * route is in neither, if an EXCLUSIONS entry has no reason, or if a new
 * dynamic `.map()` route generator appears that this file does not expand.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', '..', 'frontend', 'src');
export const ROUTES_DIR = join(SRC, 'routes');
const SETTINGS_SECTIONS_FILE = join(SRC, 'pages', 'settings', 'settingsSections.ts');

/**
 * Generic "the real screen painted" selector. A route whose default is not
 * enough overrides `ready` in ROUTES below — that is the only reason to.
 */
export const READY_DEFAULT = '#main-content :is(h1, h2, table, button, input)';

/**
 * Screens probed by tests/layout.spec.ts, in router-file order.
 *
 * ponytail: `ready` proves the route painted SOMETHING real, not that it
 * painted the right thing — a per-screen React Query error card also has a
 * heading and a Retry button and would satisfy it. The spec's URL assertion
 * plus its 404 check cover the cheap impostors (login page, NotFoundPage);
 * upgrade to per-screen selectors only if a screen is ever found erroring
 * green in CI.
 */
export const ROUTES: ReadonlyArray<{ path: string; ready?: string }> = [
  {
    path: '/reports',
    reason:
      'redirect-only: the index route maps the legacy ?tab= values onto ' +
      '/reports/<section>. Every section it can land on is probed above.',
  },
  { path: '/approvals' },
  { path: '/approvals/mine' },
  { path: '/returns/approvals' },
  { path: '/catalog' },
  { path: '/catalog/add' },
  { path: '/catalog/scorecard' },
  { path: '/catalog/quick-share' },
  { path: '/catalog/buy-desk' },
  { path: '/catalog/pricing' },
  { path: '/clinical' },
  { path: '/clinical/test' },
  { path: '/clinical/history' },
  { path: '/prescriptions' },
  { path: '/clinical/family-rx' },
  { path: '/clinical/contact-lens' },
  { path: '/customers' },
  { path: '/customers/360' },
  { path: '/customers/segmentation' },
  { path: '/customers/vip-churn-watchlist' },
  { path: '/customers/nba' },
  { path: '/customers/reactivation' },
  { path: '/customers/family-wallet' },
  { path: '/customers/cl-refill' },
  { path: '/customers/loyalty' },
  { path: '/customers/campaigns' },
  { path: '/promotions' },
  { path: '/reports/promotions' },
  { path: '/customers/whatsapp-inbox' },
  { path: '/marketing/ad-performance' },
  { path: '/customers/referrals' },
  { path: '/customers/feedback' },
  { path: '/customers/follow-ups' },
  { path: '/dashboard' },
  { path: '/notifications' },
  { path: '/finance/expenses' },
  { path: '/finance/dashboard' },
  { path: '/finance/cash-flow' },
  { path: '/finance/itc' },
  { path: '/finance/gst-cross-check' },
  { path: '/finance/cash-register' },
  { path: '/finance/blind-eod' },
  { path: '/finance/cash-reconciliation' },
  { path: '/finance/budgeting' },
  { path: '/finance/b2b-tally-export' },
  { path: '/finance/b2b-tally-worklist' },
  { path: '/hr' },
  { path: '/hr/payroll' },
  { path: '/hr/salary-setup' },
  { path: '/hr/payroll-run' },
  { path: '/incentive' },
  { path: '/incentive/leaderboard' },
  { path: '/incentive/payout' },
  { path: '/incentive/payouts' },
  { path: '/incentive/settings' },
  { path: '/inventory' },
  { path: '/inventory/replenishment' },
  { path: '/inventory/audit' },
  { path: '/inventory/opening-stock' },
  { path: '/inventory/power-grid' },
  { path: '/inventory/online-sync' },
  { path: '/online-store' },
  { path: '/online-store/products' },
  { path: '/online-store/collections' },
  { path: '/online-store/collections/browse' },
  { path: '/online-store/discount-rules' },
  { path: '/online-store/menus' },
  { path: '/online-store/images' },
  { path: '/online-store/orders' },
  { path: '/online-store/refund-reviews' },
  { path: '/online-store/customers' },
  { path: '/online-store/stock-tally' },
  { path: '/online-store/store-health' },
  { path: '/online-store/ondc' },
  { path: '/online-store/shopify' },
  { path: '/collections' },
  { path: '/collections/new' },
  { path: '/orders' },
  { path: '/estimates' },
  { path: '/walkouts' },
  { path: '/walkouts/dashboard' },
  { path: '/returns' },
  { path: '/pos/counter' },
  // The classic POS renders its own chrome, not the generic page header,
  // so the default ready selector never matches (measured: >15s timeout).
  { path: '/pos', ready: '.steps-rail, .pos-body, [class*="pos-"]' },
  { path: '/pos/new', ready: 'input[placeholder*="Scan"]' },
  { path: '/pos/delivery', ready: 'input[placeholder*="Scan"]' },
  { path: '/pos/footfall' },
  { path: '/purchase/orders' },
  { path: '/purchase/invoices' },
  { path: '/purchase/variance' },
  { path: '/purchase/suppliers' },
  { path: '/purchase/vendor-returns' },
  { path: '/purchase/analytics' },
  { path: '/purchase/grn' },
  { path: '/purchase/receive' },
  { path: '/purchase/recon-console' },
  { path: '/reports/sales' },
  { path: '/reports/inventory' },
  { path: '/reports/customers' },
  { path: '/reports/gst' },
  { path: '/reports/forecast' },
  { path: '/reports/gstr1' },
  { path: '/reports/gstr3b' },
  { path: '/reports/blueprint' },
  { path: '/reports/day-end' },
  { path: '/reports/outstanding' },
  { path: '/print' },
  { path: '/settings/profile' },
  { path: '/settings/business' },
  { path: '/settings/users' },
  { path: '/settings/categories' },
  { path: '/settings/brands' },
  { path: '/settings/lens-master' },
  { path: '/settings/lens-enums' },
  { path: '/settings/catalog-dictionary' },
  { path: '/settings/lens-pricing' },
  { path: '/settings/discounts' },
  { path: '/settings/loyalty' },
  { path: '/settings/tax-invoice' },
  { path: '/settings/hsn-rates' },
  { path: '/settings/tds-rates' },
  { path: '/settings/policies' },
  { path: '/settings/refund-policy' },
  { path: '/settings/notifications' },
  { path: '/settings/reminders' },
  { path: '/settings/integrations' },
  { path: '/settings/printers' },
  { path: '/settings/approvals' },
  { path: '/settings/agents' },
  { path: '/settings/feature-toggles' },
  { path: '/settings/audit-logs' },
  { path: '/settings/system' },
  { path: '/organization' },
  { path: '/setup' },
  { path: '/go-live' },
  { path: '/jarvis' },
  { path: '/admin/activity-log' },
  { path: '/tasks' },
  { path: '/tasks/checklists' },
  { path: '/my-work' },
  { path: '/attendance' },
  { path: '/workshop' },];

/**
 * Routes deliberately NOT probed, each with the reason. Growing this list
 * without a reason fails the guard test.
 *
 * The e2e user is SUPERADMIN (backend/scripts/seed_e2e.py), so NOTHING here
 * is excluded for RBAC — every role gate in the app admits this user.
 */
export const EXCLUSIONS: ReadonlyArray<{ path: string; reason: string }> = [
  {
    path: '/reports',
    reason:
      'redirect-only: the index route maps the legacy ?tab= values onto ' +
      '/reports/<section>. Every section it can land on is probed above.',
  },
  { path: '/finance', reason: 'redirect-only: Navigate to /finance/dashboard, which is covered' },
  { path: '/cash-flow', reason: 'redirect-only: Navigate to /finance/cash-flow, which is covered' },
  { path: '/catalog/autopilot', reason: 'redirect-only: Autopilot was deleted (PR #1042); Navigate to /catalog/add' },
  { path: '/purchase', reason: 'redirect-only: index route maps legacy ?tab= to /purchase/<section>, all covered' },
  { path: '/purchase/vendors', reason: 'redirect-only: retired alias, Navigate to /purchase/suppliers' },
  { path: '/purchase/invoices/book', reason: 'redirect-only: deep-link that Navigates to /purchase/invoices' },
  { path: '/settings', reason: 'redirect-only: index route Navigates to /settings/profile, which is covered' },
  { path: '/settings/entities', reason: 'redirect-only: Navigate to /organization, which is covered' },
  { path: '/customers/:customerId/360', reason: 'needs a seeded customer id; seed_e2e.py creates no customers' },
  { path: '/customers/:customerId/loyalty', reason: 'needs a seeded customer id; seed_e2e.py creates no customers' },
  { path: '/collections/:id', reason: 'needs a seeded collection id; seed_e2e.py creates no collections' },
  { path: '/walkouts/:walkoutId', reason: 'needs a seeded walkout id; seed_e2e.py creates no walkouts' },
  { path: '/incentive/staff/:staffId', reason: 'needs a seeded staff id; seed_e2e.py seeds only the admin user' },
  { path: '/workshop/station/:stationCode', reason: 'needs a seeded workshop station; seed_e2e.py creates none' },];

/**
 * The only `.map()` route generator in routes/*.tsx. `deriveRoutePaths`
 * expands it below; the guard fails if a second one ever appears, because
 * a generated route is invisible to a `path="..."` scan (25 real
 * /settings/<section> screens hid behind this one for exactly that reason).
 */

/**
 * Screens ALREADY broken on main when the gate was built. MEASURED across two
 * full CI runs and unioned - not guessed, and not taken from a single run.
 *
 * Quarantined, NOT excluded: the screen is still probed at all 7 widths and
 * only the exact (rule, width) entries below are tolerated. A different rule,
 * or the same rule at another width, still fails.
 *
 * WHY 'too-wide' RATHER THAN THE RAW RULE NAMES. The two CI runs disagreed
 * about WHICH rule fired for the same screen at the same width -
 * 'body-hscroll' in one, 'past-right-edge' in the other - because they are
 * two
 * symptoms of one condition: content that does not fit the viewport. Whether
 * the body ends up scrolling or an element merely pokes out depends on how far
 * the table had rendered. Pinning the exact symptom made the gate flap, and a
 * flapping required check gets switched off within a week. So the two are
 * matched as ONE family here.
 *
 * 'overlap' and 'unreachable' are NOT in that family and stay strict - they are
 * structurally different failures, and unlike a wide table you cannot scroll
 * around them.
 *
 * Every entry is a PHONE width (360 / 390 / 430). Nothing fails at 768 or
 * above: the tablet and desktop layouts are sound, and the outstanding work is
 * squarely "these screens were never made to fit a phone". /settings/business
 * is the one to fix first - overlapping controls, not just a wide table.
 *
 * The list can only SHRINK. When a recorded break stops reporting, the spec
 * FAILS and names the entry to delete, so a fix cannot leave a stale exemption
 * behind to hide the next regression.
 *
 * DO NOT ADD TO THIS LIST to make a red build green. A new break is a bug to
 * fix, not a row to add - that is how a gate rots into decoration.
 */
export const WIDTH_OVERFLOW_RULES = ['body-hscroll', 'past-right-edge'];

export const KNOWN_BROKEN: ReadonlyArray<{
  path: string;
  rule: string;
  width: number;
}> = [
  { path: '/customers/whatsapp-inbox', rule: 'too-wide', width: 360 },
  { path: '/customers/whatsapp-inbox', rule: 'too-wide', width: 390 },
  { path: '/finance/budgeting', rule: 'too-wide', width: 360 },
  { path: '/finance/cash-flow', rule: 'too-wide', width: 360 },
  { path: '/incentive', rule: 'too-wide', width: 360 },
  { path: '/incentive', rule: 'too-wide', width: 390 },
  { path: '/incentive', rule: 'too-wide', width: 430 },
  { path: '/incentive/leaderboard', rule: 'too-wide', width: 360 },
  { path: '/incentive/leaderboard', rule: 'too-wide', width: 390 },
  { path: '/incentive/leaderboard', rule: 'too-wide', width: 430 },
  { path: '/incentive/payout', rule: 'too-wide', width: 360 },
  { path: '/incentive/payout', rule: 'too-wide', width: 390 },
  { path: '/incentive/payout', rule: 'too-wide', width: 430 },
  { path: '/incentive/payouts', rule: 'too-wide', width: 360 },
  { path: '/incentive/settings', rule: 'too-wide', width: 360 },
  { path: '/incentive/settings', rule: 'too-wide', width: 390 },
  { path: '/incentive/settings', rule: 'too-wide', width: 430 },
  { path: '/inventory/power-grid', rule: 'too-wide', width: 360 },
  { path: '/inventory/power-grid', rule: 'too-wide', width: 390 },
  { path: '/inventory/power-grid', rule: 'too-wide', width: 430 },
  { path: '/inventory/replenishment', rule: 'too-wide', width: 360 },
  { path: '/inventory/replenishment', rule: 'too-wide', width: 390 },
  { path: '/pos/footfall', rule: 'too-wide', width: 360 },
  { path: '/pos/footfall', rule: 'too-wide', width: 390 },
  { path: '/promotions', rule: 'too-wide', width: 360 },
  { path: '/reports/promotions', rule: 'too-wide', width: 360 },
  { path: '/reports/promotions', rule: 'too-wide', width: 390 },
  { path: '/reports/promotions', rule: 'too-wide', width: 430 },
  { path: '/settings/business', rule: 'overlap', width: 360 },
  { path: '/settings/business', rule: 'overlap', width: 390 },
  { path: '/walkouts', rule: 'too-wide', width: 360 },
  { path: '/walkouts', rule: 'too-wide', width: 390 },
  { path: '/walkouts', rule: 'too-wide', width: 430 },
  { path: '/walkouts/dashboard', rule: 'too-wide', width: 360 },
];

/** True when this screen is already known to break this way at this width.
 *  'too-wide' covers either width-overflow symptom; anything else is exact. */
export function isKnownBreak(path: string, rule: string, width: number): boolean {
  return KNOWN_BROKEN.some(
    (k) =>
      k.path === path &&
      k.width === width &&
      (k.rule === rule ||
        (k.rule === 'too-wide' && WIDTH_OVERFLOW_RULES.includes(rule))),
  );
}

/** Recorded breaks for a screen at a width, for the fixed-detector. */
export function knownBreaksAt(path: string, width: number): string[] {
  return KNOWN_BROKEN.filter((k) => k.path === path && k.width === width).map(
    (k) => k.rule,
  );
}

/** Has this recorded break stopped reporting? */
export function stillBroken(rule: string, observed: string[]): boolean {
  return rule === 'too-wide'
    ? observed.some((r) => WIDTH_OVERFLOW_RULES.includes(r))
    : observed.includes(rule);
}

export const KNOWN_GENERATORS = ['SETTINGS_SECTIONS.map('] as const;

/** Every `.map(` call site inside routes/*.tsx, for the guard to check. */
export function findRouteGenerators(): string[] {
  const found: string[] = [];
  for (const file of readdirSync(ROUTES_DIR).filter((f) => f.endsWith('.tsx'))) {
    for (const m of readFileSync(join(ROUTES_DIR, file), 'utf8').matchAll(
      /([A-Za-z_$][\w$]*)\.map\(/g,
    )) {
      found.push(`${m[1]}.map(`);
    }
  }
  return [...new Set(found)];
}

/**
 * Re-derive every reachable app URL from the router source.
 *
 * Nesting matters: `<Route path="purchase">` wrapping `<Route path="orders">`
 * is /purchase/orders, not /orders — a flat `path="..."` grep collides those
 * two and undercounts. The scanner tracks `{}` depth and quotes so
 * `element={<Foo />}` never looks like the end of the opening tag.
 */
export function deriveRoutePaths(): string[] {
  const out: string[] = [];
  for (const file of readdirSync(ROUTES_DIR).filter((f) => f.endsWith('.tsx')).sort()) {
    const src = readFileSync(join(ROUTES_DIR, file), 'utf8');
    const stack: string[] = [];
    let i = 0;
    while (i < src.length) {
      if (src.startsWith('</Route>', i)) {
        stack.pop();
        i += 8;
        continue;
      }
      if (src.startsWith('<Route', i) && /[\s/>]/.test(src[i + 6] ?? '')) {
        let j = i + 6;
        let depth = 0;
        let quote = '';
        for (; j < src.length; j++) {
          const c = src[j];
          if (quote) {
            if (c === quote) quote = '';
            continue;
          }
          if (c === '"' || c === "'" || c === '`') quote = c;
          else if (c === '{') depth++;
          else if (c === '}') depth--;
          else if (c === '>' && depth === 0) break;
        }
        const tag = src.slice(i, j);
        const selfClosing = src[j - 1] === '/';
        const seg = /\spath="([^"]*)"/.exec(tag)?.[1];
        const parent = stack.length ? stack[stack.length - 1] : '';
        const full = seg === undefined ? parent : [parent, seg].filter(Boolean).join('/');
        if (seg !== undefined) out.push('/' + full);
        if (!selfClosing) stack.push(full);
        i = j + 1;
        continue;
      }
      i++;
    }
  }
  // Expand the one generator: <Route path="settings"> maps SETTINGS_SECTIONS
  // to a child route per section id.
  const sections = [
    ...readFileSync(SETTINGS_SECTIONS_FILE, 'utf8').matchAll(/id:\s*'([^']+)'\s+as SettingsTab/g),
  ].map((m) => `/settings/${m[1]}`);
  const at = out.indexOf('/settings');
  out.splice(at + 1, 0, ...sections);
  return out;
}

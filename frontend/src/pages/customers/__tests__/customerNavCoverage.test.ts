// Wave 2, STEP 3 — the customers module's real defect was not tabs, it was
// NINE finished screens that appeared in no menu, reachable only by typing the
// address. This guard is what stops that happening again: every fixed customer
// route must have a nav row, and that row's requireRoles must MIRROR the
// route's own allowedRoles character-for-character (a wider nav row hands a
// role a link that lands it on /unauthorized; a narrower one re-hides a screen
// that is meant to be reachable).
//
// Discriminating power: delete any `to: '/customers/...'` row from navConfig,
// or widen/narrow its requireRoles by one role, and this test fails by name.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { NAV_GROUPS } from '../../../components/shell/navConfig';

const here = path.dirname(fileURLToPath(import.meta.url));
const routeSrc = readFileSync(
  path.resolve(here, '../../../routes/customerRoutes.tsx'),
  'utf8',
);

/** Every <Route> in customerRoutes.tsx as { path, roles }, read out of the
 *  source rather than a hand-kept list — a route added without a nav row must
 *  show up here on its own. */
function declaredRoutes(): { path: string; roles: string[] }[] {
  const out: { path: string; roles: string[] }[] = [];
  const re = /path="([^"]+)"[\s\S]*?allowedRoles=\{\[([\s\S]*?)\]\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(routeSrc)) !== null) {
    const roles = m[2]
      .split(',')
      .map(r => r.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean);
    out.push({ path: '/' + m[1], roles });
  }
  return out;
}

const navRows = new Map(
  NAV_GROUPS.flatMap(g => g.items).map(i => [i.to, i.requireRoles ?? []]),
);

describe('customer routes are reachable from the menu', () => {
  const routes = declaredRoutes();

  it('parses the route file (guards the regex itself)', () => {
    // If the regex ever stops matching, every assertion below passes vacuously.
    expect(routes.length).toBeGreaterThanOrEqual(18);
    expect(routes.map(r => r.path)).toContain('/customers/segmentation');
    expect(
      routes.find(r => r.path === '/customers/segmentation')?.roles,
    ).toEqual(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']);
  });

  // Parameterised routes (/customers/:customerId, .../360, .../loyalty) are
  // reached from a customer record, never from a menu — they cannot be nav rows.
  const fixed = routes.filter(r => !r.path.includes(':'));

  it.each(fixed.map(r => r.path))('%s has a nav row', p => {
    expect(navRows.has(p)).toBe(true);
  });

  it.each(fixed.map(r => [r.path, r.roles] as const))(
    '%s nav requireRoles mirror the route gate',
    (p, roles) => {
      expect([...(navRows.get(p) ?? [])].sort()).toEqual([...roles].sort());
    },
  );
});

describe('the customer profile has a bookmarkable address', () => {
  it('/customers/:customerId renders the profile', () => {
    const profile = declaredRoutes().find(r => r.path === '/customers/:customerId');
    expect(profile).toBeDefined();
    // Same gate as the /360 form it aliases.
    expect(profile!.roles).toEqual(
      declaredRoutes().find(r => r.path === '/customers/:customerId/360')!.roles,
    );
  });

  it('static customer screens still out-rank the :customerId pattern', () => {
    // React Router ranks a literal segment above a dynamic one, so this is a
    // statement about the route TABLE, not about matching: the fixed screens
    // must keep their own entries rather than being folded into the pattern.
    for (const p of ['/customers/segmentation', '/customers/loyalty', '/customers/nba']) {
      expect(declaredRoutes().map(r => r.path)).toContain(p);
    }
  });
});

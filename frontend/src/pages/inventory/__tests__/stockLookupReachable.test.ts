// D7b (owner ruling 2026-09-29): counter staff get a READ-ONLY stock lookup -
// this shop, every other shop and in transit, no cost / supplier / bill. Audit
// row F46: "Sales staff have no stock screen at all."
//
// D7b-7, the frontend half (the backend half is
// backend/tests/test_counter_stock_lookup.py). Today:
//   - INVENTORY_MODULE_ROLES (../inventoryRoles.ts) leaves out every counter
//     role, so the whole Inventory module is closed to them;
//   - navConfig.ts has no stock row a SALES_STAFF / CASHIER / OPTOMETRIST sees;
//   - no /inventory/lookup route, and so no layout-gate row for it.
//
// Each `it.fails` reproduces the finding and FAILS THE SUITE the moment it
// starts passing (vitest's strict xfail): the fix flips it to `it`.
// SALES_CASHIER is not listed here: the backend folds it into SALES_STAFF at
// sign-in (user_roles.normalize_roles), so no browser session ever holds it.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { NAV_GROUPS } from '../../../components/shell/navConfig';
import * as inventoryRoles from '../inventoryRoles';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel: string) => readFileSync(path.resolve(here, rel), 'utf8');

const LOOKUP = '/inventory/lookup';
const COUNTER = ['SALES_STAFF', 'CASHIER', 'OPTOMETRIST'];

const navRow = () => NAV_GROUPS.flatMap(g => g.items).find(i => i.to === LOOKUP);

/** allowedRoles of `<Route path="<seg>">` in inventoryRoutes.tsx, whether the
 *  gate is an inline array or a list exported from inventoryRoles.ts. */
function routeRoles(seg: string): string[] | undefined {
  const src = read('../../../routes/inventoryRoutes.tsx');
  const esc = seg.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
  const m = new RegExp(`path="${esc}"[\\s\\S]*?allowedRoles=\\{([\\s\\S]*?)\\}\\s*>`).exec(src);
  if (!m) return undefined;
  const expr = m[1].trim();
  if (expr.startsWith('[')) {
    return expr
      .slice(1, -1)
      .split(',')
      .map(r => r.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean);
  }
  const named = (inventoryRoles as Record<string, unknown>)[expr];
  return Array.isArray(named) ? (named as string[]) : undefined;
}

describe('D7b-7: counter staff reach a read-only stock lookup', () => {
  it('reads a route gate out of inventoryRoutes.tsx (guards the parser)', () => {
    // If the parser ever stops matching, the it.fails below pass vacuously.
    expect(routeRoles('inventory/power-grid')).toEqual(inventoryRoles.POWER_GRID_ROLES);
  });

  it.fails('the menu has a stock lookup row for every counter role', () => {
    const row = navRow();
    expect(row).toBeDefined();
    for (const r of COUNTER) expect(row!.requireRoles).toContain(r);
  });

  it.fails('/inventory/lookup admits the counter roles and the menu row mirrors it', () => {
    const roles = routeRoles('inventory/lookup');
    expect(roles).toBeDefined();
    for (const r of COUNTER) expect(roles).toContain(r);
    expect([...(navRow()?.requireRoles ?? [])].sort()).toEqual([...roles!].sort());
  });

  it.fails('the layout gate probes /inventory/lookup (e2e/fixtures/routes.ts ROUTES)', () => {
    const src = read('../../../../../e2e/fixtures/routes.ts');
    const routes = src.slice(src.indexOf('export const ROUTES'), src.indexOf('export const EXCLUSIONS'));
    expect(routes).toMatch(/path:\s*'\/inventory\/lookup'/);
  });
});

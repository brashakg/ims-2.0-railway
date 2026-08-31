/**
 * Guard: no screen escapes the layout gate.
 *
 * Mirrors the backend convention (a new route without an `rbac_policy` row
 * fails CI). Adding a screen to frontend/src/routes/*.tsx without adding it
 * to ROUTES or EXCLUSIONS in fixtures/routes.ts fails HERE, by name.
 *
 * Pure file reading — no browser, no backend, milliseconds.
 */
import { test, expect } from '@playwright/test';
import {
  ROUTES,
  EXCLUSIONS,
  KNOWN_GENERATORS,
  deriveRoutePaths,
  findRouteGenerators,
} from '../fixtures/routes';

const inventory = new Map<string, 'probed' | 'excluded'>([
  ...ROUTES.map((r) => [r.path, 'probed'] as const),
  ...EXCLUSIONS.map((e) => [e.path, 'excluded'] as const),
]);

test('every app route is either probed or excluded with a reason', () => {
  const derived = deriveRoutePaths();

  const missing = derived.filter((p) => !inventory.has(p));
  expect(
    missing,
    `${missing.length} route(s) in frontend/src/routes/*.tsx are not in the layout ` +
      `gate's inventory:\n${missing.map((p) => `  ${p}`).join('\n')}\n\n` +
      `Add each to e2e/fixtures/routes.ts — to ROUTES (with a \`ready\` override only ` +
      `if the default selector is not enough), or, if it genuinely cannot be probed, ` +
      `to EXCLUSIONS with a one-line reason.`,
  ).toEqual([]);

  const stale = [...inventory.keys()].filter((p) => !derived.includes(p));
  expect(
    stale,
    `${stale.length} inventory entr(ies) name a route that no longer exists:\n` +
      `${stale.map((p) => `  ${p}`).join('\n')}\nRemove them from e2e/fixtures/routes.ts.`,
  ).toEqual([]);
});

test('every exclusion carries a reason', () => {
  const unexplained = EXCLUSIONS.filter((e) => e.reason.trim().length < 10).map((e) => e.path);
  expect(
    unexplained,
    `Excluding a screen without saying why reads as "all green". Give each a reason:\n` +
      unexplained.map((p) => `  ${p}`).join('\n'),
  ).toEqual([]);
});

test('no unregistered dynamic route generator', () => {
  const unknown = findRouteGenerators().filter(
    (g) => !(KNOWN_GENERATORS as readonly string[]).includes(g),
  );
  expect(
    unknown,
    `New .map() route generator(s) in frontend/src/routes/*.tsx: ${unknown.join(', ')}.\n` +
      `Generated routes are invisible to a path="..." scan. Teach deriveRoutePaths() in ` +
      `e2e/fixtures/routes.ts to expand it, then add it to KNOWN_GENERATORS.`,
  ).toEqual([]);
});

// Coverage floor / exclusion ceiling. Adding screens never trips this; QUIETLY
// SHRINKING the gate does -- moving probed screens into EXCLUSIONS one
// plausible reason at a time is how a green gate stops meaning anything.
test('coverage has not silently shrunk', () => {
  expect(
    { probedAtLeast: ROUTES.length >= 134, exclusions: EXCLUSIONS.length },
    'The layout gate covered 134 screens with 14 exclusions when it was armed. ' +
      'If an exclusion is genuinely new and justified, raise the ceiling here in the ' +
      'same commit that adds it -- deliberately, not by accident.',
  ).toEqual({ probedAtLeast: true, exclusions: 14 });
});

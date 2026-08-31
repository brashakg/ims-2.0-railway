// Tripwire for the 2026-08-31 regression that blanked EVERY page on phones.
//
// At <=767px the rail is a position:fixed drawer, so it occupies no grid
// cell and `main` is the only in-flow child of .app-shell — it auto-places
// into the FIRST column. If that column is sized 0 (the old `0 1fr`, written
// when the rail still sat in the grid as a bottom bar), main computes to 0px
// wide and the app renders blank. Measured in a real browser: 0px vs 375px.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8');

describe('phone shell layout', () => {
  it('gives .app-shell a single full-width column at <=767px', () => {
    const start = css.indexOf('@media (max-width: 767px), (max-height: 500px)');
    expect(start).toBeGreaterThan(-1);
    const block = css.slice(start, start + 1200);
    const rule = block.match(/\.app-shell\s*\{[^}]*\}/);
    expect(rule).not.toBeNull();
    const cols = rule![0].match(/grid-template-columns:\s*([^;]+);/);
    expect(cols).not.toBeNull();
    // A zero-width first column here means `main` is invisible.
    expect(cols![1].trim()).not.toMatch(/^0(px)?\s/);
    expect(cols![1].trim()).toBe('1fr');
  });
});

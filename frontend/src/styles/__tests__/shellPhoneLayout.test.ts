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

describe('tablet / iPad shell layout', () => {
  it('lets .top-nav shrink below its content so the PAGE never scrolls sideways', () => {
    // .top-nav is a grid item of .app-shell. A grid item defaults to
    // min-width:auto and refuses to shrink below its content, so with every
    // nav group rendered the 1fr column stretched to ~1366-1617px and the
    // whole page scrolled horizontally at iPad-landscape width — which also
    // stole 10px of height and broke the POS "one screen, no scrolling" rule.
    // .top-nav-menu already has overflow-x:auto; it only gets to use it once
    // the nav itself is allowed to be narrower than its content.
    const start = css.indexOf('.top-nav {');
    expect(start).toBeGreaterThan(-1);
    const rule = css.slice(start, css.indexOf('}', start));
    expect(rule).toContain('min-width: 0;');
  });
});

describe('phone shell layout', () => {
  it('gives .app-shell a single full-width column at <=767px', () => {
    const start = css.indexOf('@media (max-width: 767px), (max-height: 500px)');
    expect(start).toBeGreaterThan(-1);
    const block = css.slice(start, start + 2600);
    const rule = block.match(/\.app-shell\s*\{[^}]*\}/);
    expect(rule).not.toBeNull();
    const cols = rule![0].match(/grid-template-columns:\s*([^;]+);/);
    expect(cols).not.toBeNull();
    // A zero-width first column here means `main` is invisible.
    expect(cols![1].trim()).not.toMatch(/^0(px)?\s/);
    expect(cols![1].trim()).toBe('1fr');
  });

  it('gives .app-shell a single full-height row at <=767px', () => {
    // Same trap on the row axis: the desktop template is `auto minmax(0,1fr)`
    // for [top-nav, main]. On a phone the top-nav is display:none, so main
    // lands in the `auto` row and is sized to its CONTENT — measured 465px
    // inside an 812px shell, with dead space below and clipped modals.
    const start = css.indexOf('@media (max-width: 767px), (max-height: 500px)');
    const block = css.slice(start, start + 2600);
    const rule = block.match(/\.app-shell\s*\{[^}]*\}/);
    expect(rule).not.toBeNull();
    const rows = rule![0].match(/grid-template-rows:\s*([^;]+);/);
    expect(rows).not.toBeNull();
    expect(rows![1]).not.toMatch(/auto/);
    expect(rows![1].trim()).toBe('minmax(0, 1fr)');
  });
});

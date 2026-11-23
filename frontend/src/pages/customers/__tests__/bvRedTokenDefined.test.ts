// Codex PR-1428 r3 R3-C: `bg-bv-red` (no shade) has no colour token in
// index.css, so a `bg-bv-red text-white` button rendered white text on a
// transparent background -- the Retry of the NBA and Reactivation modals was
// invisible. Every bv-red utility must name a shade the stylesheet defines.
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) return n === '__tests__' ? [] : files(p);
    return /\.tsx?$/.test(n) ? [p] : [];
  });
}

describe('bv-red utilities name a defined shade', () => {
  it('no className uses a bare bv-red colour', () => {
    const css = readFileSync(path.join(src, 'index.css'), 'utf8');
    const defined = new Set([...css.matchAll(/--color-(bv-red(?:-\d+)?):/g)].map((m) => m[1]));
    const bad = files(src).flatMap((f) =>
      [...readFileSync(f, 'utf8').matchAll(/\b(?:bg|text|border|ring|from|to|via|outline|fill|stroke)-(bv-red(?:-\d+)?)(?![\w-])/g)]
        .filter((m) => !defined.has(m[1]))
        .map((m) => `${path.relative(src, f)}: ${m[0]}`));
    expect(bad).toEqual([]);
  });
});

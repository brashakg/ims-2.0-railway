/**
 * The unit label, printed: measured in a real browser, not regex-matched.
 *
 * The label is a 100 x 15 mm page with a 70 mm printable window (TSC TE244,
 * owner ruling 2026-09-28). jsdom has no layout, so the unit tests could only
 * check class names -- and a 15-character barcode cut every MRP of Rs 1,000 or
 * more ("MRP ₹8,9…") while they stayed green. This renders THE label module
 * (frontend/src/components/labels/unitLabel.ts, imported, never copied) in
 * Chromium under print media and measures what would print:
 *
 *   - the barcode text, the MRP and the size are shown whole, inside the window;
 *   - nothing is painted over anything else (a clamped long model or colour
 *     must be cut, not overprint the next line);
 *   - the text beside the widest barcode a label accepts keeps >= 12 mm.
 *
 * Read-only: no app page, no backend, no login state is used. The filename
 * puts it in the parallel `layout` project.
 */
import { test, expect } from '@playwright/test';
import {
  labelProblem,
  unitLabelsDocument,
  type UnitLabelData,
} from '../../frontend/src/components/labels/unitLabel';

/** The longest run of `ch` that labelProblem accepts (capped, so a guard that
 *  accepts everything fails below instead of looping). */
function longestAccepted(ch: string): string {
  let n = 0;
  while (n < 80 && !labelProblem(ch.repeat(n + 1))) n += 1;
  return ch.repeat(n);
}

const WIDEST = longestAccepted('A'); // widest bars a label takes
// Most characters a label takes: Code 128 packs digit PAIRS, so 30 digits make
// the same 55.05 mm of bars as 15 letters (29 or 31 need a code switch).
const LONGEST_TEXT = '7'.repeat(30);

const BARCODES = [
  'BC-ABCDEFABCDEF', // the widest IMS mints: vendors/numbering.py BC- fallback
  WIDEST,
  LONGEST_TEXT,
  'BV--E5145C6A', // a receipt unit
  '2000000012345', // EAN-13
  '7', // the narrowest bars: the MRP is wider than them
];
const MRPS: Array<[number, string]> = [
  [8990, 'MRP ₹8,990'],
  [12990, 'MRP ₹12,990'],
  [129990, 'MRP ₹1,29,990'],
  [1234567, 'MRP ₹12,34,567'],
];
const LONG = {
  brand: 'Ray-Ban',
  model: 'RB5154 Clubmaster Optics Extra Long Model Name',
  colour: '2000 Black/Gold Havana Tortoise',
  size: '51-21-145',
};

type Box = { l: number; t: number; r: number; b: number };
type Measured = {
  barcode: string;
  win: Box;
  info: Box;
  whole: Array<{ what: string; text: string; box: Box; cut: boolean }>;
  overprints: string[];
};

test('the widest barcodes a label accepts are the ones this test prints', () => {
  // The BC- fallback (15 letters, 55.05 mm of bars) must print; one more
  // letter (57.8 mm) would leave the text under 12 mm.
  expect(labelProblem('BC-ABCDEFABCDEF')).toBe('');
  expect(WIDEST.length).toBe(15);
  expect(labelProblem(LONGEST_TEXT)).toBe('');
  expect(labelProblem('7'.repeat(32))).not.toBe('');
});

test('every label shows its barcode, MRP and size whole, and paints nothing over anything', async ({
  page,
}) => {
  const labels: UnitLabelData[] = [];
  for (const barcode of BARCODES) for (const [mrp] of MRPS) labels.push({ barcode, mrp, ...LONG });
  await page.emulateMedia({ media: 'print' });
  await page.setContent(unitLabelsDocument(labels));

  const measured: Measured[] = await page.evaluate(() => {
    const TOL = 0.5; // px: sub-pixel rounding
    const box = (r: DOMRect) => ({ l: r.left, t: r.top, r: r.right, b: r.bottom });
    const meet = (a: Box, b: Box): Box => ({
      l: Math.max(a.l, b.l),
      t: Math.max(a.t, b.t),
      r: Math.min(a.r, b.r),
      b: Math.min(a.b, b.b),
    });
    const empty = (a: Box) => a.r - a.l <= TOL || a.b - a.t <= TOL;
    return Array.from(document.querySelectorAll('.lbl')).map((lbl) => {
      const win = lbl.querySelector('.win') as HTMLElement;
      const winBox = box(win.getBoundingClientRect());
      const code = win.querySelector('.code') as HTMLElement;
      const info = win.querySelector('.info') as HTMLElement;
      const leaves = [
        ...Array.from(code.children),
        ...Array.from(info.children),
      ] as HTMLElement[];
      // What each leaf actually paints: its line boxes (a text rect is the
      // font's whole ascent-to-descent, taller than the line-height the lines
      // stack by, so it is cut to its line), clipped by its own box when it
      // clips its overflow, and by the window (overflow: hidden).
      const painted = (el: HTMLElement): Box[] => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const cs = getComputedStyle(el);
        const clips = cs.overflowX !== 'visible' || cs.overflowY !== 'visible';
        const own = box(el.getBoundingClientRect());
        const lh = parseFloat(cs.lineHeight);
        return Array.from(range.getClientRects())
          .map((r) => {
            const k = Math.floor(((r.top + r.bottom) / 2 - own.t) / lh);
            const line = { l: r.left, r: r.right, t: own.t + k * lh, b: own.t + (k + 1) * lh };
            return meet(clips ? meet(line, own) : line, winBox);
          })
          .filter((r) => !empty(r));
      };
      const name = (el: Element) =>
        el.tagName === 'svg' ? 'bars' : `"${(el.textContent || '').slice(0, 24)}"`;
      const overprints: string[] = [];
      for (const a of leaves) {
        if (a.tagName === 'svg') continue;
        for (const p of painted(a)) {
          for (const b of leaves) {
            if (b === a) continue;
            if (!empty(meet(p, box(b.getBoundingClientRect())))) {
              overprints.push(`${name(a)} paints over ${name(b)}`);
            }
          }
        }
      }
      const whole = [
        ['barcode text', win.querySelector('.txt')],
        ['MRP', win.querySelector('.mrp')],
        ['size', win.querySelector('.size')],
      ].map(([what, el]) => {
        const e = el as HTMLElement | null;
        if (!e) return { what: what as string, text: '(no such line)', box: winBox, cut: true };
        const b = box(e.getBoundingClientRect());
        const inside =
          b.l >= winBox.l - TOL && b.r <= winBox.r + TOL && b.t >= winBox.t - TOL && b.b <= winBox.b + TOL;
        return {
          what: what as string,
          text: e.textContent || '',
          box: b,
          cut: e.scrollWidth > e.clientWidth || !inside,
        };
      });
      return {
        barcode: (code.querySelector('.txt')?.textContent || '').trim(),
        win: winBox,
        info: box(info.getBoundingClientRect()),
        whole,
        overprints: Array.from(new Set(overprints)),
      };
    });
  });

  expect(measured).toHaveLength(BARCODES.length * MRPS.length);
  const PX_PER_MM = 96 / 25.4;
  const problems: string[] = [];
  measured.forEach((m, i) => {
    const [, mrpText] = MRPS[i % MRPS.length];
    const at = `${m.barcode} / ${mrpText}`;
    for (const w of m.whole) if (w.cut) problems.push(`${at}: the ${w.what} "${w.text}" is cut`);
    const mrp = m.whole.find((w) => w.what === 'MRP')!;
    if (mrp.text !== mrpText) problems.push(`${at}: the MRP reads "${mrp.text}"`);
    const size = m.whole.find((w) => w.what === 'size')!;
    if (size.text !== LONG.size) problems.push(`${at}: the size reads "${size.text}"`);
    const infoMm = (m.info.r - m.info.l) / PX_PER_MM;
    if (infoMm < 12) problems.push(`${at}: ${infoMm.toFixed(2)} mm left for brand, model, colour and size`);
    for (const o of m.overprints) problems.push(`${at}: ${o}`);
  });
  expect(problems, problems.join('\n')).toEqual([]);
});

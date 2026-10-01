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
 *   - the text beside the widest barcode a label accepts keeps >= 12 mm;
 *   - a size too long for its line is cut on that line, inside the window;
 *   - a SAVED offset moves the printed window that far from the paper's left
 *     edge, and N labels print as exactly N pages of 100 x 15 mm (the PDF
 *     Chromium's print pipeline makes, page count and page size read back).
 *
 * Read-only: no app page, no backend, no login state is used. The filename
 * puts it in the parallel `layout` project.
 */
import { test, expect } from '@playwright/test';
import {
  labelProblem,
  setLabelOffsetMm,
  testLabelDocument,
  unitLabelsDocument,
  type UnitLabelData,
} from '../../frontend/src/components/labels/unitLabel';

// The offset is kept in browser storage, which this Node runner lacks: the
// module gets a Map-backed one (as the vitest suites do), so an offset saved
// through setLabelOffsetMm is the one unitLabelsDocument prints at.
const saved = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  writable: true,
  value: {
    getItem: (k: string) => saved.get(k) ?? null,
    setItem: (k: string, v: string) => void saved.set(k, String(v)),
    removeItem: (k: string) => void saved.delete(k),
  },
});
test.beforeEach(() => saved.clear());

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
  [1499.5, 'MRP ₹1,499.50'], // paise print, never rounded (owner 2026-10-01)
  [1234567.89, 'MRP ₹12,34,567.89'], // the widest price line
];
const LONG = {
  brand: 'Ray-Ban',
  model: 'RB5154 Clubmaster Optics Extra Long Model Name',
  colour: '2000 Black/Gold Havana Tortoise',
  size: '51-21-145',
};
// A size longer than its line (free text: products.size / attributes): cut on
// its own line, never over the colour or off the label.
const LONG_SIZE = 'Eye 52 / Bridge 18 / Temple 145 / Medium Asian Fit / Titanium Flex Hinge';

type Box = { l: number; t: number; r: number; b: number };
type Line = { what: string; text: string; box: Box; inside: boolean; overflows: boolean };
type Measured = {
  barcode: string;
  win: Box;
  info: Box;
  lines: Line[];
  overprints: string[];
};
/** One label to print, and what it must read: its MRP line, and whether its
 *  size fits its line (a size too long for it is cut, inside the window). */
type Case = { unit: UnitLabelData; mrpText: string; sizeFits: boolean };

test('the widest barcodes a label accepts are the ones this test prints', () => {
  // The BC- fallback (15 letters, 55.05 mm of bars) must print; one more
  // letter (57.8 mm) would leave the text under 12 mm.
  expect(labelProblem('BC-ABCDEFABCDEF')).toBe('');
  expect(WIDEST.length).toBe(15);
  expect(labelProblem(LONGEST_TEXT)).toBe('');
  expect(labelProblem('7'.repeat(32))).not.toBe('');
});

test('every label shows its barcode and MRP whole, its size on its own line, and paints nothing over anything', async ({
  page,
}) => {
  const cases: Case[] = [];
  for (const barcode of BARCODES) {
    for (const [mrp, mrpText] of MRPS) cases.push({ unit: { barcode, mrp, ...LONG }, mrpText, sizeFits: true });
    cases.push({ unit: { barcode, mrp: 8990, ...LONG, size: LONG_SIZE }, mrpText: 'MRP ₹8,990', sizeFits: false });
  }
  await page.emulateMedia({ media: 'print' });
  await page.setContent(unitLabelsDocument(cases.map((c) => c.unit)));

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
      const lines = [
        ['barcode text', win.querySelector('.txt')],
        ['MRP', win.querySelector('.mrp')],
        ['size', win.querySelector('.size')],
      ].map(([what, el]) => {
        const e = el as HTMLElement | null;
        if (!e) return { what: what as string, text: '(no such line)', box: winBox, inside: false, overflows: true };
        const b = box(e.getBoundingClientRect());
        return {
          what: what as string,
          text: e.textContent || '',
          box: b,
          // on the label: inside the 70 mm window, and a line high
          inside:
            b.l >= winBox.l - TOL &&
            b.r <= winBox.r + TOL &&
            b.t >= winBox.t - TOL &&
            b.b <= winBox.b + TOL &&
            b.b - b.t > TOL,
          overflows: e.scrollWidth > e.clientWidth || e.scrollHeight > e.clientHeight + 1,
        };
      });
      return {
        barcode: (code.querySelector('.txt')?.textContent || '').trim(),
        win: winBox,
        info: box(info.getBoundingClientRect()),
        lines,
        overprints: Array.from(new Set(overprints)),
      };
    });
  });

  expect(measured).toHaveLength(cases.length);
  const PX_PER_MM = 96 / 25.4;
  const problems: string[] = [];
  measured.forEach((m, i) => {
    const { unit, mrpText, sizeFits } = cases[i];
    const at = `${m.barcode} / ${mrpText} / size "${unit.size}"`;
    const line = (what: string) => m.lines.find((w) => w.what === what)!;
    for (const what of ['barcode text', 'MRP']) {
      const w = line(what);
      if (w.overflows || !w.inside) problems.push(`${at}: the ${what} "${w.text}" is cut`);
    }
    if (line('MRP').text !== mrpText) problems.push(`${at}: the MRP reads "${line('MRP').text}"`);
    const size = line('size');
    if (size.text !== unit.size) problems.push(`${at}: the size reads "${size.text}"`);
    if (!size.inside) problems.push(`${at}: the size line is not on the label`);
    // A size that fits shows whole; one that does not is cut on its own line
    // (and this case must really be too long, or it proves nothing).
    if (size.overflows === sizeFits) {
      problems.push(`${at}: the size is ${sizeFits ? 'cut' : 'not cut, so this case tests nothing'}`);
    }
    const infoMm = (m.info.r - m.info.l) / PX_PER_MM;
    if (infoMm < 12) problems.push(`${at}: ${infoMm.toFixed(2)} mm left for brand, model, colour and size`);
    for (const o of m.overprints) problems.push(`${at}: ${o}`);
  });
  expect(problems, problems.join('\n')).toEqual([]);
});

test('a saved offset moves the printed window, and N labels print as N pages of 100 x 15 mm', async ({
  page,
}) => {
  // Measured, not regex-matched: with the window no longer absolutely placed
  // every offset printed at 0 mm; a label 0.5 mm too tall fed a second page
  // per label; the default body margin added a page and shifted every label.
  const MM_PER_PX = 25.4 / 96;
  const PT_PER_MM = 72 / 25.4;
  const TOL_MM = 0.1;
  const units: UnitLabelData[] = BARCODES.slice(0, 3).map((barcode) => ({ barcode, mrp: 8990, ...LONG }));
  const problems: string[] = [];
  for (const offset of [0, 7.3, 30]) {
    expect(setLabelOffsetMm(offset)).toBe(true);
    const docs: Array<[string, string, number]> = [
      ['stock labels', unitLabelsDocument(units), units.length],
      ['test label', testLabelDocument(), 1],
    ];
    for (const [what, html, n] of docs) {
      const at = `${what}, offset ${offset} mm`;
      await page.emulateMedia({ media: 'print' });
      await page.setContent(html);
      // Where each label and its window sit, in mm from the paper's top-left.
      const placed = await page.evaluate((k) => {
        const mm = (r: DOMRect) => ({
          l: r.left * k,
          t: (r.top + window.scrollY) * k,
          w: r.width * k,
          h: r.height * k,
        });
        return Array.from(document.querySelectorAll('.lbl')).map((lbl) => ({
          lbl: mm(lbl.getBoundingClientRect()),
          win: mm((lbl.querySelector('.win') as HTMLElement).getBoundingClientRect()),
        }));
      }, MM_PER_PX);
      if (placed.length !== n) problems.push(`${at}: ${placed.length} labels in the document, not ${n}`);
      placed.forEach(({ lbl, win }, i) => {
        const want: Array<[string, number, number]> = [
          ['label left edge', lbl.l, 0],
          ['label top', lbl.t, i * 15],
          ['label width', lbl.w, 100],
          ['label height', lbl.h, 15],
          ['window from the paper edge', win.l, offset],
          ['window top', win.t, i * 15],
          ['window width', win.w, 70],
        ];
        for (const [name, got, exp] of want) {
          if (Math.abs(got - exp) > TOL_MM) {
            problems.push(`${at}, label ${i + 1}: ${name} ${got.toFixed(2)} mm, not ${exp} mm`);
          }
        }
      });
      // The print pipeline itself: one PDF page per label, each 100 x 15 mm.
      const pdf = (await page.pdf({ preferCSSPageSize: true })).toString('latin1');
      const pages = pdf.match(/\/Type\s*\/Page\b(?!s)/g) || [];
      if (pages.length !== n) problems.push(`${at}: ${n} label(s) printed as ${pages.length} page(s)`);
      for (const media of pdf.match(/\/MediaBox\s*\[[^\]]*\]/g) || []) {
        const [, , w, h] = media.replace(/[^\d.\s]/g, ' ').trim().split(/\s+/).map(Number);
        // Chromium sizes the page in its own device units: within 0.25 mm.
        if (Math.abs(w / PT_PER_MM - 100) > 0.25 || Math.abs(h / PT_PER_MM - 15) > 0.25) {
          problems.push(`${at}: a page is ${(w / PT_PER_MM).toFixed(2)} x ${(h / PT_PER_MM).toFixed(2)} mm`);
        }
      }
    }
  }
  expect(problems, problems.join('\n')).toEqual([]);
});

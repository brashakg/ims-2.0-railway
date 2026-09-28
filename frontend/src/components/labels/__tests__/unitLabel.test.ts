// ============================================================================
// The ONE unit label: TSC TE244, 100 x 15 mm stock, 70 mm printable window,
// printed through the normal Windows driver (owner ruling 2026-09-28).
// ============================================================================
// What the physical label needs, pinned by hand-written numbers:
//   - one page per label, page = 100mm x 15mm, no trailing break (a trailing
//     break feeds a blank label);
//   - content confined to a 70 mm window, flush left by default, shifted by
//     the saved offset (the shop calibrates it on the real printer);
//   - Code 128 of the UNIT barcode with no bar narrower than 2 dots at 203 dpi
//     (0.2502 mm) and a quiet zone;
//   - barcode text, brand + model, colour + size, MRP.

// This Node/jsdom runner ships a partial localStorage (no clear/setItem): the
// repo's Map-backed stand-in (see HeldBillsScoping.test.ts).
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  code128Modules,
  getLabelOffsetMm,
  printUnitLabels,
  setLabelOffsetMm,
  testLabelDocument,
  unitLabelsDocument,
} from '../unitLabel';

const CARRERA = {
  barcode: 'BV--E5145C6A',
  brand: 'Carrera',
  model: 'CA 8895',
  colour: '807',
  size: '54',
  mrp: 8990,
};
const TWO_DOTS_MM = (2 * 25.4) / 203; // 0.25025

function parse(html: string) {
  return new DOMParser().parseFromString(html, 'text/html');
}

beforeEach(() => {
  localStorage.clear();
});

describe('the unit label page', () => {
  it('is one 100 x 15 mm page per label with no trailing page break', () => {
    const html = unitLabelsDocument([CARRERA, { ...CARRERA, barcode: 'BV--91FA3858' }]);
    expect(html).toMatch(/@page\s*{\s*size:\s*100mm 15mm;\s*margin:\s*0;?\s*}/);
    const labels = parse(html).querySelectorAll('.lbl');
    expect(labels).toHaveLength(2);
    // Break BETWEEN labels only; never after the last one.
    expect(html).toMatch(/\.lbl \+ \.lbl\s*{[^}]*break-before:\s*page/);
    expect(html).not.toMatch(/break-after:\s*(page|always)/);
  });

  it('confines content to a 70 mm window, flush left by default', () => {
    const html = unitLabelsDocument([CARRERA]);
    expect(html).toMatch(/\.win\s*{[^}]*width:\s*70mm/);
    expect(parse(html).querySelector('.win')?.getAttribute('style')).toBe('left:0mm');
  });

  it('shifts the window by the saved offset, clamped to the stock', () => {
    setLabelOffsetMm(12.5);
    expect(getLabelOffsetMm()).toBe(12.5);
    expect(parse(unitLabelsDocument([CARRERA])).querySelector('.win')?.getAttribute('style')).toBe(
      'left:12.5mm',
    );
    setLabelOffsetMm(80); // 100 - 70 = 30 is the most the window can move
    expect(getLabelOffsetMm()).toBe(30);
    setLabelOffsetMm(-4);
    expect(getLabelOffsetMm()).toBe(0);
  });

  it('prints the unit barcode text, brand + model, colour + size and MRP', () => {
    const text = parse(unitLabelsDocument([CARRERA])).body.textContent || '';
    expect(text).toContain('BV--E5145C6A');
    expect(text).toContain('Carrera CA 8895');
    expect(text).toContain('807');
    expect(text).toContain('54');
    expect(text).toMatch(/MRP\s*₹\s*8,990/);
  });

  it('lets a long brand + model or colour + size take two lines, and never clips the MRP', () => {
    // ~20 mm is left beside the bars: "Ray-Ban RB5154 Clubmaster Optics" or
    // "2000 Black/Gold / 51-21-145" on one ellipsised line lost the SIZE.
    // Measured in Chromium at 203 dpi: 2 + 2 + 1 lines at 6.5 pt fit 15 mm.
    const html = unitLabelsDocument([CARRERA]);
    const info = parse(html).querySelector('.info')!;
    const [title, variant, mrp] = Array.from(info.children);
    expect(title.className).toContain('two');
    expect(variant.className).toContain('two');
    expect(mrp.className).not.toContain('two');
    expect(html).toMatch(/\.two\s*{[^}]*-webkit-line-clamp:\s*2/);
    expect(html).toMatch(/\.info\s*{[^}]*font-size:\s*6\.5pt/);
  });

  it('escapes product text (it lands in innerHTML of the print window)', () => {
    const html = unitLabelsDocument([{ ...CARRERA, brand: '<img src=x onerror=alert(1)>' }]);
    expect(parse(html).querySelector('img')).toBeNull();
  });
});

describe('the barcode', () => {
  it('is Code 128: a start code, then 11-module symbols, then the stop code', () => {
    const m = code128Modules('BV--E5145C6A');
    expect(['11010000100', '11010010000', '11010011100']).toContain(m.slice(0, 11));
    expect(m.slice(-13)).toBe('1100011101011');
    expect((m.length - 13) % 11).toBe(0);
  });

  it('encodes the unit it is printed for (different unit, different bars)', () => {
    expect(code128Modules('BV--00F1D2CC')).not.toBe(code128Modules('BV--91FA3858'));
  });

  it("the bars on each label ARE that unit's barcode", () => {
    const other = { ...CARRERA, barcode: 'BV--91FA3858' };
    const svgs = parse(unitLabelsDocument([CARRERA, other])).querySelectorAll('.win svg');
    const drawn = (svg: Element) => {
      const n = Number(svg.getAttribute('viewBox')!.split(' ')[2]);
      const m = Array(n).fill('0');
      svg.querySelectorAll('rect').forEach((r) => {
        const x = Number(r.getAttribute('x'));
        for (let i = 0; i < Number(r.getAttribute('width')); i += 1) m[x + i] = '1';
      });
      return m.join('').replace(/^0+|0+$/g, '');
    };
    expect(drawn(svgs[0])).toBe(code128Modules('BV--E5145C6A'));
    expect(drawn(svgs[1])).toBe(code128Modules('BV--91FA3858'));
  });

  it('draws no bar narrower than 2 dots at 203 dpi and keeps a quiet zone', () => {
    const doc = parse(unitLabelsDocument([CARRERA]));
    const svg = doc.querySelector('.win svg') as SVGSVGElement;
    const modules = Number(svg.getAttribute('viewBox')!.split(' ')[2]);
    const widthMm = parseFloat(svg.getAttribute('width')!);
    expect(svg.getAttribute('width')).toMatch(/mm$/);
    const moduleMm = widthMm / modules;
    expect(moduleMm).toBeGreaterThanOrEqual(TWO_DOTS_MM - 1e-3);
    // Every bar is a whole number of modules (so a whole number of dots).
    const widths = Array.from(svg.querySelectorAll('rect')).map((r) => Number(r.getAttribute('width')));
    expect(widths.length).toBeGreaterThan(10);
    widths.forEach((w) => expect(Number.isInteger(w) && w >= 1).toBe(true));
    // 10-module quiet zone either side: the first and last bars sit inside it.
    const xs = Array.from(svg.querySelectorAll('rect')).map((r) => Number(r.getAttribute('x')));
    expect(Math.min(...xs)).toBe(10);
    const last = svg.querySelectorAll('rect')[widths.length - 1];
    expect(modules - (Number(last.getAttribute('x')) + Number(last.getAttribute('width')))).toBe(10);
    // ...and the whole symbol fits the 70 mm window.
    expect(widthMm).toBeLessThan(70);
  });
});

describe('the test label', () => {
  it('outlines the 70 mm window at the saved offset so the shop can calibrate', () => {
    setLabelOffsetMm(4);
    const doc = parse(testLabelDocument());
    expect(doc.querySelectorAll('.lbl')).toHaveLength(1);
    expect(doc.querySelector('.win')?.getAttribute('style')).toBe('left:4mm');
    expect(doc.querySelector('.win.outline')).not.toBeNull();
    expect(doc.body.textContent).toMatch(/offset 4 mm/i);
  });
});

describe('one stock-label renderer', () => {
  it('the workshop label module no longer builds a product / frame / CL-box tag', async () => {
    // Its 50 x 25 mm QZ/ZPL frame tag was a second stock-label renderer on the
    // wrong stock, reachable from no screen. Stock labels = unitLabel.ts only.
    const templates: Record<string, unknown> = await import('../labelTemplates');
    const printers: Record<string, unknown> = await import('../printLabel');
    ['buildProductLabel', 'frameHtml', 'frameZpl', 'clHtml', 'clZpl'].forEach((k) =>
      expect(templates[k], k).toBeUndefined(),
    );
    expect(printers.printProductLabel).toBeUndefined();
  });
});

describe('the print dialog', () => {
  it('opens ONCE per print (a second dialog is a second set of labels)', () => {
    vi.useFakeTimers();
    const win = {
      document: { open: vi.fn(), write: vi.fn(), close: vi.fn() },
      focus: vi.fn(),
      print: vi.fn(),
      onload: null as null | (() => void),
    };
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    expect(printUnitLabels([CARRERA]).method).toBe('html');
    win.onload?.(); // the browser fired load...
    vi.advanceTimersByTime(500); // ...and the no-onload safety timer ran too
    expect(win.print).toHaveBeenCalledTimes(1);
    expect(win.document.write.mock.calls[0][0]).toContain('BV--E5145C6A');
    vi.useRealTimers();
  });
});

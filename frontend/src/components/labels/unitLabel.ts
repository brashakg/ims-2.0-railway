// ============================================================================
// IMS 2.0 - THE unit label (every stock-label print door uses this)
// ============================================================================
// Owner ruling 2026-09-28: the shops print barcodes on a TSC TE244 thermal
// printer, label stock 100 x 15 mm with a 70 mm printable area, full gumming,
// through the NORMAL Windows driver (no QZ Tray, no raw printer commands).
//
// So a label is an HTML page exactly 100 x 15 mm (CSS @page size); everything
// printed sits in a 70 mm window, flush left unless the shop saved an offset
// (Settings > Printers: the printable side of the stock is calibrated on the
// real printer). The Code 128 bars are drawn in mm with the narrowest bar at
// exactly 2 printer dots (203 dpi), so the driver rasterises every bar to a
// whole number of dots and a scanner reads it.

import JsBarcode from 'jsbarcode';
import { printHtmlFallback, type PrintResult } from '../../services/printWindow';

export const LABEL_WIDTH_MM = 100;
export const LABEL_HEIGHT_MM = 15;
export const PRINTABLE_MM = 70;
/** Narrowest bar: 2 dots of a 203 dpi head. */
const MODULE_MM = (2 * 25.4) / 203;
const QUIET_MODULES = 10;
/** Inside the window: its right padding, and the gap between bars and text. */
const WIN_PAD_RIGHT_MM = 1;
const GAP_MM = 1.5;
/** Room the text beside the bars keeps (brand + model, colour, size). The MRP
 *  sits under the barcode text, so no barcode can squeeze the price. */
const MIN_INFO_MM = 12;
/** Widest bar block (quiet zones included) that leaves the text its room. The
 *  longest unit barcode IMS mints (the 15-character BC- fallback) is 55.05 mm;
 *  e2e/tests/layout-unit-label.spec.ts measures the printed label in Chromium. */
const MAX_BARS_MM = PRINTABLE_MM - WIN_PAD_RIGHT_MM - GAP_MM - MIN_INFO_MM;
const OFFSET_KEY = 'ims.unitLabel.offsetMm';

export interface UnitLabelData {
  barcode: string;
  brand?: string;
  model?: string;
  colour?: string;
  size?: string;
  mrp?: number | null;
}

// ---- the saved window offset (per computer: it belongs to the printer here) --

export function getLabelOffsetMm(): number {
  try {
    return clampOffset(Number(localStorage.getItem(OFFSET_KEY)) || 0);
  } catch {
    return 0;
  }
}

export function setLabelOffsetMm(mm: number): void {
  try {
    localStorage.setItem(OFFSET_KEY, String(clampOffset(mm)));
  } catch {
    /* storage blocked: the default (flush left) stays in force */
  }
}

function clampOffset(mm: number): number {
  const max = LABEL_WIDTH_MM - PRINTABLE_MM;
  return Math.min(max, Math.max(0, Math.round((Number(mm) || 0) * 10) / 10));
}

// ---- Code 128 ------------------------------------------------------------

/** Code 128 of `value` as a module string, '1' = bar (start..stop, no quiet zone). */
export function code128Modules(value: string): string {
  const out: { encodings?: Array<{ data: string }> } = {};
  JsBarcode(out, value, { format: 'CODE128' });
  return (out.encodings ?? []).map((e) => e.data).join('');
}

/** Why this barcode cannot go on a label ('' when it can). A label whose bars
 *  do not carry the unit's barcode must never print, nor be recorded as sent. */
export function labelProblem(barcode: string): string {
  if (!String(barcode ?? '').trim()) return 'No barcode';
  let modules: number;
  try {
    modules = code128Modules(barcode).length;
  } catch {
    return 'Barcode has characters a label cannot carry';
  }
  return (modules + 2 * QUIET_MODULES) * MODULE_MM > MAX_BARS_MM ? 'Barcode too long for the label' : '';
}

/** Throws on a barcode Code 128 cannot carry: callers check labelProblem first. */
function barcodeSvg(value: string, heightMm: number): string {
  const bars = code128Modules(value);
  const quiet = '0'.repeat(QUIET_MODULES);
  const m = quiet + bars + quiet;
  const rects: string[] = [];
  for (let i = 0; i < m.length; ) {
    if (m[i] !== '1') {
      i += 1;
      continue;
    }
    let j = i;
    while (m[j] === '1') j += 1;
    rects.push(`<rect x="${i}" y="0" width="${j - i}" height="1"/>`);
    i = j;
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${(m.length * MODULE_MM).toFixed(3)}mm" ` +
    `height="${heightMm}mm" viewBox="0 0 ${m.length} 1" preserveAspectRatio="none" ` +
    `shape-rendering="crispEdges">${rects.join('')}</svg>`
  );
}

// ---- the page ----------------------------------------------------------------

function esc(v: unknown): string {
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** The MRP exactly as stored, never rounded (owner 2026-10-01): whole rupees
 *  print without decimals, a price with paise prints them (1,499.50). No price
 *  for a unit with no MRP: an import with no price stores 0, and "MRP ₹0" is a
 *  wrong price. */
function mrpText(mrp?: number | null): string {
  const n = Number(mrp);
  if (mrp == null || !(n > 0)) return '';
  const decimals = Number.isInteger(n) ? 0 : 2;
  return `MRP ₹${n.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: 20 })}`;
}

function labelHtml(u: UnitLabelData, offsetMm: number, extraClass = ''): string {
  const title = [u.brand, u.model].filter(Boolean).join(' ');
  return (
    `<div class="lbl"><div class="win${extraClass}" style="left:${offsetMm}mm">` +
    `<div class="code">${barcodeSvg(u.barcode, 8)}<div class="txt">${esc(u.barcode)}</div>` +
    `<div class="mrp">${esc(mrpText(u.mrp))}</div></div>` +
    `<div class="info"><div class="b two">${esc(title)}</div><div class="two">${esc(u.colour)}</div>` +
    `<div class="size">${esc(u.size)}</div></div></div></div>`
  );
}

function wrap(body: string, title: string): string {
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
@page { size: ${LABEL_WIDTH_MM}mm ${LABEL_HEIGHT_MM}mm; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; color: #000; }
body { font-family: Arial, Helvetica, sans-serif; }
.lbl { position: relative; width: ${LABEL_WIDTH_MM}mm; height: ${LABEL_HEIGHT_MM}mm; overflow: hidden; }
.lbl + .lbl { break-before: page; page-break-before: always; }
.win { position: absolute; top: 0; width: ${PRINTABLE_MM}mm; height: ${LABEL_HEIGHT_MM}mm; overflow: hidden;
  display: flex; align-items: center; gap: ${GAP_MM}mm; padding: 0.8mm ${WIN_PAD_RIGHT_MM}mm 0.8mm 0; }
.win.outline { border: 0.25mm solid #000; }
.code { flex: none; text-align: center; white-space: nowrap; }
.code svg { display: block; }
.txt { font-family: 'Courier New', monospace; font-size: 7pt; line-height: 1.1; letter-spacing: 0.3px; }
/* Under the barcode text, whole: the column is as wide as the bars, or as the
   price when a short barcode's bars are narrower. */
.mrp { font-size: 6.5pt; line-height: 1.1; font-weight: 700; }
.info { flex: 1; min-width: 0; font-size: 6.5pt; line-height: 1.15; }
.info div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* Brand + model and colour wrap once, then cut; the size keeps a line of its
   own so a long colour never pushes it off. 2 + 2 + 1 lines fit the 15 mm. */
.info .two { white-space: normal; overflow-wrap: anywhere; display: -webkit-box;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.b { font-weight: 700; }
</style></head><body>${body}</body></html>`;
}

/** A printable document: one 100 x 15 mm page per unit, at the saved offset. */
export function unitLabelsDocument(units: UnitLabelData[]): string {
  const offset = getLabelOffsetMm();
  return wrap(units.map((u) => labelHtml(u, offset)).join(''), 'Stock labels');
}

/** One label that outlines the 70 mm window, for calibrating the offset. */
export function testLabelDocument(): string {
  const offset = getLabelOffsetMm();
  return wrap(
    labelHtml(
      { barcode: 'TEST-0123456', brand: 'TEST LABEL', model: `offset ${offset} mm`, mrp: 1234 },
      offset,
      ' outline',
    ),
    'Test label',
  );
}

/** Open the Windows print dialog for these units' labels -- or refuse the
 *  whole batch, opening nothing, when any unit's barcode cannot be printed. */
export function printUnitLabels(units: UnitLabelData[]): PrintResult {
  const bad = units.find((u) => labelProblem(u.barcode));
  if (bad) {
    return { method: 'failed', message: `${labelProblem(bad.barcode)} (${bad.barcode || 'a unit'}): no label printed.` };
  }
  const r = printHtmlFallback(unitLabelsDocument(units));
  return r.method === 'html' ? r : { ...r, message: `${r.message} Allow pop-ups for IMS and try again.` };
}

export function printTestLabel(): PrintResult {
  return printHtmlFallback(testLabelDocument());
}

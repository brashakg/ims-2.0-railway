// ============================================================================
// IMS 2.0 - Thermal Label Templates (ZPL builders + HTML renderers)
// ============================================================================
// Each label type provides BOTH:
//   - a ZPL string builder  -> sent to QZ Tray for silent raw thermal printing
//   - an HTML string render  -> used for the print-window fallback + preview
//     (works before QZ / a signing cert is configured)
//
// CRITICAL UX (owner's explicit requirement):
//   EVERY label leaves real blank ruled space for handwritten notes, and the
//   READY / pickup label additionally carries a blank "Follow-up" section
//   (lines to write a follow-up date / note by hand). These are empty boxes
//   / lines, not pre-filled text.
//
// Sizing: small thermal labels default to ~50x25mm; the traveler/work-order
// label is larger at ~75x50mm. ZPL assumes 203dpi (8 dots/mm), the common
// resolution for Zebra / TSC desktop label printers.

import JsBarcode from 'jsbarcode';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LabelType = 'traveler' | 'stage' | 'frame' | 'cl' | 'ready';

export interface JobLabelData {
  job_id: string;
  job_number?: string;
  barcode_value?: string;
  order_number?: string;
  customer_name?: string;
  customer_phone?: string;
  rx?: { right?: string; left?: string; available?: boolean };
  frame?: string;
  lens?: string;
  fitting_instructions?: string;
  special_notes?: string;
  promised_date?: string;
  store_name?: string;
  store_id?: string;
  stage?: string;
  stage_label?: string;
  next_stage?: string | null;
  include_followup?: boolean;
}

export interface ProductLabelData {
  barcode_value: string;
  /** Issuing store name (resolved from the stock unit's store_id by the
   *  backend) -- printed so a tag shows which store holds the unit. */
  store_name?: string;
  name?: string;
  brand?: string;
  sku?: string;
  category?: string;
  mrp?: number | string;
  price_label?: string;
  is_contact_lens?: boolean;
  batch_code?: string;
  expiry?: string;
  cl?: {
    modality?: string;
    base_curve?: string;
    diameter?: string;
    power?: string;
    cyl?: string;
    axis?: string;
    add?: string;
    color?: string;
    pack_size?: number | string;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Escape a value for safe embedding inside HTML (label render is innerHTML). */
function esc(v: unknown): string {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * ZPL is its own escaping world: `^` and `~` are command prefixes. We strip
 * them from field data and keep things ASCII so a stray glyph can't break a
 * print. (We deliberately do NOT emit the rupee glyph anywhere.)
 */
function zplSafe(v: unknown): string {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[\^~]/g, ' ').replace(/[^\x20-\x7E]/g, '');
}

function fmtDate(value?: string): string {
  if (!value) return '';
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

/** Generate a CODE128 barcode as an inline SVG string for HTML labels. */
export function barcodeSvg(value: string, height = 40): string {
  try {
    // JsBarcode needs a real SVG element; build a detached one and serialise.
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    JsBarcode(svg, value || '0', {
      format: 'CODE128',
      width: 1.5,
      height,
      displayValue: true,
      fontSize: 11,
      margin: 2,
    });
    return new XMLSerializer().serializeToString(svg);
  } catch {
    // Fallback: just show the value as text so the label still prints.
    return `<div style="font-family:monospace;font-size:11px">${esc(value)}</div>`;
  }
}

const A_DOT = 8; // dots per mm at 203dpi

// ---------------------------------------------------------------------------
// Shared CSS for the HTML fallback / preview labels.
// ---------------------------------------------------------------------------

export function labelBaseCss(widthMm: number, heightMm: number): string {
  return `
    * { box-sizing: border-box; }
    body { margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; color: #000; }
    .label {
      width: ${widthMm}mm;
      min-height: ${heightMm}mm;
      padding: 2mm;
      border: 1px dashed #bbb;
      page-break-after: always;
    }
    .lbl-title { font-weight: 700; font-size: 9pt; text-transform: uppercase; letter-spacing: .5px; }
    .lbl-row { font-size: 8pt; line-height: 1.25; }
    .lbl-strong { font-weight: 700; }
    .lbl-mono { font-family: 'Courier New', monospace; }
    .lbl-muted { color: #444; font-size: 7pt; }
    .lbl-barcode { text-align: center; margin: 1mm 0; }
    .lbl-barcode svg { max-width: 100%; }
    .lbl-stage {
      display: inline-block; border: 2px solid #000; border-radius: 3px;
      padding: 1mm 2mm; font-weight: 800; font-size: 11pt; text-transform: uppercase;
    }
    /* Blank ruled space for handwritten notes (pen / marker). */
    .lbl-notes { margin-top: 1.5mm; }
    .lbl-notes .lbl-cap { font-size: 6.5pt; color: #666; text-transform: uppercase; }
    .lbl-line { border-bottom: 1px solid #999; height: 4.5mm; }
    .lbl-box { border: 1px solid #999; height: 9mm; margin-top: 1mm; }
    .lbl-followup { margin-top: 2mm; border: 1px solid #000; padding: 1mm; }
    .lbl-followup .lbl-cap { font-weight: 700; font-size: 7pt; text-transform: uppercase; }
    @media print { .label { border: none; } @page { margin: 0; } }
  `;
}

/** Reusable blank handwriting block (HTML). `lines` blank ruled lines. */
function notesHtml(caption: string, lines = 2): string {
  const lineDivs = Array.from({ length: lines })
    .map(() => '<div class="lbl-line"></div>')
    .join('');
  return `<div class="lbl-notes"><div class="lbl-cap">${esc(caption)}</div>${lineDivs}</div>`;
}

// ===========================================================================
// JOB TRAVELER / WORK-ORDER  (~75x50mm)
// ===========================================================================

export function travelerZpl(d: JobLabelData): string {
  const bc = zplSafe(d.barcode_value || d.job_number || d.job_id);
  const w = 75 * A_DOT;
  const h = 50 * A_DOT;
  // Note the blank box at the bottom drawn with ^GB for handwritten notes.
  return [
    '^XA',
    `^PW${w}`,
    `^LL${h}`,
    '^CI28',
    '^CF0,26',
    // STORE-SPECIFIC: print the issuing store's name (resolved from the job's
    // store_id by the backend). Neutral fallback -- never a fixed brand that
    // would mislabel another store's work order.
    `^FO16,16^FD${zplSafe(d.store_name || 'Work Order')}^FS`,
    '^CF0,22',
    `^FO16,46^FDJob ${zplSafe(d.job_number || d.job_id)}^FS`,
    `^FO16,72^FDOrder ${zplSafe(d.order_number || '')}^FS`,
    // Barcode (CODE128)
    `^FO16,98^BY2^BCN,70,Y,N,N^FD${bc}^FS`,
    '^CF0,20',
    `^FO16,190^FD${zplSafe(d.customer_name || '')}  ${zplSafe(d.customer_phone || '')}^FS`,
    `^FO16,214^FDFrame: ${zplSafe(d.frame || '')}^FS`,
    `^FO16,236^FDLens: ${zplSafe(d.lens || '')}^FS`,
    `^FO16,258^FDR: ${zplSafe(d.rx?.right || '')}^FS`,
    `^FO16,278^FDL: ${zplSafe(d.rx?.left || '')}^FS`,
    `^FO16,300^FDPromised: ${zplSafe(fmtDate(d.promised_date))}^FS`,
    '^CF0,16',
    `^FO16,326^FDNOTES (write below):^FS`,
    // Blank box for handwriting
    '^FO16,344^GB560,70,2^FS',
    '^XZ',
  ].join('\n');
}

export function travelerHtml(d: JobLabelData): string {
  return `
  <div class="label" style="width:75mm;min-height:50mm">
    <div class="lbl-title">${d.store_name ? esc(d.store_name) + ' - ' : ''}Work Order</div>
    <div class="lbl-row"><span class="lbl-strong lbl-mono">${esc(d.job_number || d.job_id)}</span>
      ${d.order_number ? `&nbsp;&middot;&nbsp;Order ${esc(d.order_number)}` : ''}</div>
    <div class="lbl-barcode">${barcodeSvg(d.barcode_value || d.job_number || d.job_id, 38)}</div>
    <div class="lbl-row"><span class="lbl-strong">${esc(d.customer_name || '')}</span>
      ${d.customer_phone ? `&nbsp;${esc(d.customer_phone)}` : ''}</div>
    ${d.frame ? `<div class="lbl-row">Frame: ${esc(d.frame)}</div>` : ''}
    ${d.lens ? `<div class="lbl-row">Lens: ${esc(d.lens)}</div>` : ''}
    ${d.rx?.right ? `<div class="lbl-row">R: ${esc(d.rx.right)}</div>` : ''}
    ${d.rx?.left ? `<div class="lbl-row">L: ${esc(d.rx.left)}</div>` : ''}
    ${d.promised_date ? `<div class="lbl-row">Promised: <span class="lbl-strong">${esc(fmtDate(d.promised_date))}</span></div>` : ''}
    ${d.stage_label ? `<div class="lbl-row lbl-muted">Stage: ${esc(d.stage_label)}</div>` : ''}
    ${notesHtml('Notes (write below)', 3)}
  </div>`;
}

// ===========================================================================
// STAGE STICKER  (~50x25mm) - big stage badge + barcode + a note line
// ===========================================================================

export function stageZpl(d: JobLabelData): string {
  const bc = zplSafe(d.barcode_value || d.job_number || d.job_id);
  const w = 50 * A_DOT;
  const h = 25 * A_DOT;
  return [
    '^XA',
    `^PW${w}`,
    `^LL${h}`,
    '^CI28',
    '^CF0,34',
    `^FO12,10^FD${zplSafe(d.stage_label || d.stage || '')}^FS`,
    '^CF0,20',
    `^FO12,52^FD${zplSafe(d.job_number || d.job_id)}^FS`,
    `^FO12,76^BY2^BCN,50,Y,N,N^FD${bc}^FS`,
    // single handwriting line
    '^FO12,150^GB376,1,2^FS',
    '^CF0,14',
    `^FO12,156^FDNote:^FS`,
    '^XZ',
  ].join('\n');
}

export function stageHtml(d: JobLabelData): string {
  return `
  <div class="label" style="width:50mm;min-height:25mm">
    <div class="lbl-stage">${esc(d.stage_label || d.stage || 'Stage')}</div>
    <div class="lbl-row lbl-mono" style="margin-top:1mm">${esc(d.job_number || d.job_id)}</div>
    ${d.customer_name ? `<div class="lbl-row">${esc(d.customer_name)}</div>` : ''}
    <div class="lbl-barcode">${barcodeSvg(d.barcode_value || d.job_number || d.job_id, 30)}</div>
    ${notesHtml('Note', 1)}
  </div>`;
}

// ===========================================================================
// FRAME TAG  (dumbbell ~ small) - we use a compact 50x25mm tag
// ===========================================================================

export function frameZpl(d: ProductLabelData): string {
  const bc = zplSafe(d.barcode_value);
  const w = 50 * A_DOT;
  const h = 25 * A_DOT;
  return [
    '^XA',
    `^PW${w}`,
    `^LL${h}`,
    '^CI28',
    '^CF0,22',
    `^FO12,10^FD${zplSafe(d.brand || '')}^FS`,
    '^CF0,18',
    `^FO12,36^FD${zplSafe(d.name || d.sku || '')}^FS`,
    `^FO12,60^BY2^BCN,48,Y,N,N^FD${bc}^FS`,
    '^CF0,24',
    `^FO12,150^FD${zplSafe(d.price_label || '')}^FS`,
    '^XZ',
  ].join('\n');
}

export function frameHtml(d: ProductLabelData): string {
  return `
  <div class="label" style="width:50mm;min-height:25mm">
    ${d.brand ? `<div class="lbl-title">${esc(d.brand)}</div>` : ''}
    ${d.name ? `<div class="lbl-row">${esc(d.name)}</div>` : ''}
    ${d.sku ? `<div class="lbl-row lbl-mono lbl-muted">${esc(d.sku)}</div>` : ''}
    <div class="lbl-barcode">${barcodeSvg(d.barcode_value, 32)}</div>
    ${d.price_label ? `<div class="lbl-row lbl-strong" style="font-size:11pt">${esc(d.price_label)}</div>` : ''}
    ${d.store_name ? `<div class="lbl-muted">${esc(d.store_name)}</div>` : ''}
    ${notesHtml('Note', 1)}
  </div>`;
}

// ===========================================================================
// CONTACT-LENS BOX  (~50x25mm) - CL identity + batch/expiry + barcode
// ===========================================================================

export function clZpl(d: ProductLabelData): string {
  const bc = zplSafe(d.barcode_value);
  const cl = d.cl || {};
  const spec = [
    cl.power ? `PWR ${cl.power}` : '',
    cl.base_curve ? `BC ${cl.base_curve}` : '',
    cl.diameter ? `DIA ${cl.diameter}` : '',
  ]
    .filter(Boolean)
    .join('  ');
  const w = 50 * A_DOT;
  const h = 25 * A_DOT;
  return [
    '^XA',
    `^PW${w}`,
    `^LL${h}`,
    '^CI28',
    '^CF0,20',
    `^FO12,8^FD${zplSafe((d.brand || '') + ' ' + (d.name || ''))}^FS`,
    '^CF0,18',
    `^FO12,32^FD${zplSafe(spec)}^FS`,
    `^FO12,54^FDExp: ${zplSafe(d.expiry || '')}  Lot: ${zplSafe(d.batch_code || '')}^FS`,
    `^FO12,76^BY2^BCN,46,Y,N,N^FD${bc}^FS`,
    '^XZ',
  ].join('\n');
}

export function clHtml(d: ProductLabelData): string {
  const cl = d.cl || {};
  const spec = [
    cl.power ? `PWR ${cl.power}` : '',
    cl.base_curve ? `BC ${cl.base_curve}` : '',
    cl.diameter ? `DIA ${cl.diameter}` : '',
    cl.modality ? cl.modality : '',
  ]
    .filter(Boolean)
    .join(' &middot; ');
  return `
  <div class="label" style="width:50mm;min-height:25mm">
    <div class="lbl-title">${esc((d.brand || '') + ' ' + (d.name || ''))}</div>
    ${spec ? `<div class="lbl-row">${spec}</div>` : ''}
    <div class="lbl-row lbl-muted">Exp: ${esc(d.expiry || '__________')} &nbsp; Lot: ${esc(d.batch_code || '________')}</div>
    <div class="lbl-barcode">${barcodeSvg(d.barcode_value, 30)}</div>
    ${d.store_name ? `<div class="lbl-muted">${esc(d.store_name)}</div>` : ''}
    ${notesHtml('Note', 1)}
  </div>`;
}

// ===========================================================================
// READY / PICKUP  (~50x30mm) - includes a blank FOLLOW-UP section by hand
// ===========================================================================

export function readyZpl(d: JobLabelData): string {
  const bc = zplSafe(d.barcode_value || d.job_number || d.job_id);
  const w = 50 * A_DOT;
  const h = 30 * A_DOT; // a touch taller to fit the follow-up box
  return [
    '^XA',
    `^PW${w}`,
    `^LL${h}`,
    '^CI28',
    '^CF0,30',
    '^FO12,10^FDREADY FOR PICKUP^FS',
    '^CF0,20',
    `^FO12,46^FD${zplSafe(d.customer_name || '')}^FS`,
    `^FO12,70^FD${zplSafe(d.job_number || d.job_id)}^FS`,
    `^FO12,92^BY2^BCN,46,Y,N,N^FD${bc}^FS`,
    // Follow-up box drawn with ^GB + caption -> handwritten follow-up date/note
    '^CF0,16',
    '^FO12,168^FDFollow-up (date / note):^FS',
    '^FO12,186^GB376,46,2^FS',
    '^XZ',
  ].join('\n');
}

export function readyHtml(d: JobLabelData): string {
  return `
  <div class="label" style="width:50mm;min-height:30mm">
    <div class="lbl-stage" style="font-size:10pt">Ready for Pickup</div>
    <div class="lbl-row lbl-strong" style="margin-top:1mm">${esc(d.customer_name || '')}</div>
    ${d.customer_phone ? `<div class="lbl-row">${esc(d.customer_phone)}</div>` : ''}
    <div class="lbl-row lbl-mono">${esc(d.job_number || d.job_id)}</div>
    <div class="lbl-barcode">${barcodeSvg(d.barcode_value || d.job_number || d.job_id, 30)}</div>
    <div class="lbl-followup">
      <div class="lbl-cap">Follow-up (date / note)</div>
      <div class="lbl-line"></div>
      <div class="lbl-line"></div>
    </div>
  </div>`;
}

// ===========================================================================
// Dispatch helpers
// ===========================================================================

export interface BuiltLabel {
  zpl: string;
  html: string;
  widthMm: number;
  heightMm: number;
  title: string;
}

/** Build a job label (traveler / stage / ready) -> ZPL + HTML. */
export function buildJobLabel(type: 'traveler' | 'stage' | 'ready', d: JobLabelData): BuiltLabel {
  if (type === 'traveler') {
    return { zpl: travelerZpl(d), html: travelerHtml(d), widthMm: 75, heightMm: 50, title: 'Work Order' };
  }
  if (type === 'ready') {
    return { zpl: readyZpl(d), html: readyHtml(d), widthMm: 50, heightMm: 30, title: 'Ready for Pickup' };
  }
  return { zpl: stageZpl(d), html: stageHtml(d), widthMm: 50, heightMm: 25, title: 'Stage Sticker' };
}

/** Build a product label (frame tag or CL box) -> ZPL + HTML. */
export function buildProductLabel(d: ProductLabelData): BuiltLabel {
  if (d.is_contact_lens) {
    return { zpl: clZpl(d), html: clHtml(d), widthMm: 50, heightMm: 25, title: 'Contact Lens Box' };
  }
  return { zpl: frameZpl(d), html: frameHtml(d), widthMm: 50, heightMm: 25, title: 'Frame Tag' };
}

/** Wrap a label's HTML body into a full printable document string. */
export function wrapLabelDocument(built: BuiltLabel, copies = 1): string {
  const body = Array.from({ length: Math.max(1, copies) })
    .map(() => built.html)
    .join('\n');
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${esc(
    built.title,
  )}</title><style>${labelBaseCss(built.widthMm, built.heightMm)}</style></head><body>${body}</body></html>`;
}

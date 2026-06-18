// ============================================================================
// IMS 2.0 - Shared statutory print primitives
// ============================================================================
// Frontend twins of backend/api/services/print_legal.py. The TSX renderers
// here consume the same `LegalHeader` / `StaffHeader` data shapes the
// backend builds and emit the bordered, ALL-CAPS, sans-only statutory
// layout described in docs/design/STATUTORY_NOTES.md.
//
// These components are SHARED by all 6 in-use print templates (Tax Invoice,
// Thermal Receipt, Rx Card, Lens Job Card, GRN, Z-Report). They render
// PURE statutory aesthetic -- no BV red banners, no brand emphasis. The
// editor drawer UI uses BV brand tokens; printed docs do not.

import { ReactNode } from 'react';

// ---------------------------------------------------------------------------
// Types -- aligned with backend services/print_legal.py
// ---------------------------------------------------------------------------

export interface CopyMarker {
  mode: 'rule_48' | 'rule_55' | 'internal' | 'none';
  active: string;
  active_index: number;
  labels: string[];
  marks: string[];
  rendered: string;
}

export interface LegalHeaderData {
  doc_type: string;
  doc_number: string;
  doc_date: string;
  copy_marker: CopyMarker;
  legal_name: string;
  trade_name: string;
  header_subtitle: string;
  supplier_kv: Array<[string, string]>;
  store_name: string;
  store_address: string;
  store_phone: string;
  store_email: string;
  place_of_supply: string;
  state_code: string;
  state_name: string;
  gstin: string;
  reverse_charge: boolean;
  meta: Array<[string, string]>;
  signatory_name: string;
  signatory_designation: string;
  footer_terms: string;
  logo_url: string;
  brand_label: string;
  retention_years: number;
}

export interface StaffHeaderData {
  doc_type: string;
  doc_number: string;
  doc_date: string;
  trade_name: string;
  header_subtitle: string;
  branch_label: string;
  logo_url: string;
  brand_label: string;
  meta: Array<[string, string]>;
  signatory_name: string;
  signatory_designation: string;
  footer_terms: string;
  copy_marker: CopyMarker;
}

export interface EntityLike {
  legal_name?: string;
  name?: string;
  pan?: string;
  cin?: string;
  llpin?: string;
  registered_address?: string;
  registered_phone?: string;
  registered_email?: string;
  website?: string;
  /** Legacy / ad-hoc top-level logo. The Organization module stores the logo
   *  NESTED under invoice.logo_url -- buildLegalHeader reads both. */
  logo_url?: string;
  /** Entity invoice identity (Organization module). The brand logo lives here. */
  invoice?: {
    logo_url?: string;
    legal_display_name?: string;
    signatory_name?: string;
    signatory_designation?: string;
    footer_text?: string;
    terms?: string;
  };
  gstins?: Array<{
    gstin: string;
    state_code: string;
    state_name?: string;
    is_primary?: boolean;
  }>;
}

export interface StoreLike {
  name?: string;
  store_name?: string;
  store_code?: string;
  code?: string;
  trade_name?: string;
  brand?: string;
  address?: string;
  street?: string;
  address_line_1?: string;
  city?: string;
  state?: string;
  state_name?: string;
  state_code?: string;
  pincode?: string;
  phone?: string;
  email?: string;
  gstin?: string;
}

// Human-readable brand label keyed on the store.brand enum.
const BRAND_LABEL: Record<string, string> = {
  BETTER_VISION: 'Better Vision',
  WIZOPT: 'WizOpt',
};

/** Resolve the entity logo: nested invoice.logo_url first (the Organization
 *  module writes it there), then a top-level logo_url fallback. */
function logoFromEntity(entity: EntityLike | null | undefined): string {
  if (!entity) return '';
  const nested = entity.invoice?.logo_url;
  if (nested && String(nested).trim()) return String(nested).trim();
  return pick(entity as Record<string, unknown>, 'logo_url');
}

function brandLabelOf(store: StoreLike | null | undefined): string {
  const b = String(store?.brand || '').trim().toUpperCase();
  return BRAND_LABEL[b] || '';
}

export interface OverrideFields {
  header_subtitle?: string;
  declaration_text?: string;
  signatory_name?: string;
  signatory_designation?: string;
  drug_licence_no?: string;
  ncahp_uid?: string;
  dmc_reg?: string;
  footer_terms?: string;
  logo_url?: string;
  retention_years?: number;
  reverse_charge_default?: boolean;
}

// ---------------------------------------------------------------------------
// Indian-numbering amount in words (mirror of backend amount_in_words)
// ---------------------------------------------------------------------------

const ONES = [
  '', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
  'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
  'Seventeen', 'Eighteen', 'Nineteen',
];
const TENS = [
  '', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety',
];

function two(n: number): string {
  if (n <= 0) return '';
  if (n < 20) return ONES[n];
  const t = Math.floor(n / 10);
  const u = n % 10;
  return u === 0 ? TENS[t] : TENS[t] + '-' + ONES[u];
}

function three(n: number): string {
  if (n <= 0) return '';
  if (n < 100) return two(n);
  const h = Math.floor(n / 100);
  const rest = n % 100;
  let out = ONES[h] + ' Hundred';
  if (rest) out += ' ' + two(rest);
  return out;
}

export function amountInWords(value: number | string | null | undefined, paiseArg = 0): string {
  let n: number;
  if (typeof value === 'string') {
    const parsed = parseFloat(value);
    if (Number.isNaN(parsed)) return 'Indian Rupees Zero Only';
    n = parsed;
  } else if (typeof value === 'number' && Number.isFinite(value)) {
    n = value;
  } else {
    return 'Indian Rupees Zero Only';
  }

  let sign = '';
  if (n < 0) {
    sign = 'Less: ';
    n = -n;
  }
  let rupees = Math.floor(n);
  let paise = Math.round((n - rupees) * 100);
  if (paise >= 100) {
    rupees += 1;
    paise = 0;
  }
  if (paiseArg) paise = paiseArg;
  if (paise >= 100) {
    rupees += Math.floor(paise / 100);
    paise = paise % 100;
  }

  const cr = Math.floor(rupees / 10000000);
  const restA = rupees % 10000000;
  const lakh = Math.floor(restA / 100000);
  const restB = restA % 100000;
  const thou = Math.floor(restB / 1000);
  const rest = restB % 1000;

  const parts: string[] = [];
  if (cr) parts.push(two(cr) + ' Crore');
  if (lakh) parts.push(two(lakh) + ' Lakh');
  if (thou) parts.push(two(thou) + ' Thousand');
  if (rest) parts.push(three(rest));

  const rupeesWords = parts.length ? parts.join(' ') : 'Zero';
  let out = 'Indian Rupees ' + rupeesWords;
  if (paise) out += ' and ' + two(paise) + ' Paise';
  out += ' Only';
  return sign + out;
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function formatDate(value: Date | string | null | undefined): string {
  if (!value) return '';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getDate()).padStart(2, '0')}-${MONTHS[d.getMonth()]}-${d.getFullYear()}`;
}

export function formatDateTimeIST(value: Date | string | null | undefined): string {
  if (!value) return '';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${formatDate(d)} ${hh}:${mm} IST`;
}

// Indian-numbering currency with the Rupee glyph and tabular-nums display.
export function inr(value: number, opts?: { withPaise?: boolean }): string {
  const withPaise = opts?.withPaise !== false;
  const minFrac = withPaise ? 2 : 0;
  const maxFrac = withPaise ? 2 : 0;
  return '₹' + Number(value || 0).toLocaleString('en-IN', {
    minimumFractionDigits: minFrac,
    maximumFractionDigits: maxFrac,
  });
}

// ---------------------------------------------------------------------------
// Copy markers (Rule 48 invoice / Rule 55 challan)
// ---------------------------------------------------------------------------

const RULE_48_LABELS: [string, string, string] = [
  'ORIGINAL FOR RECIPIENT',
  'DUPLICATE FOR TRANSPORTER',
  'TRIPLICATE FOR SUPPLIER',
];
const RULE_55_LABELS: [string, string, string] = [
  'ORIGINAL FOR CONSIGNEE',
  'DUPLICATE FOR TRANSPORTER',
  'TRIPLICATE FOR CONSIGNOR',
];

const COPY_INDEX: Record<string, number> = {
  ORIGINAL: 0,
  DUPLICATE: 1,
  TRIPLICATE: 2,
};

export function copyMarker(
  copyType: string = 'ORIGINAL',
  mode: 'rule_48' | 'rule_55' | 'internal' | 'none' = 'rule_48'
): CopyMarker {
  if (mode === 'internal') {
    return {
      mode,
      active: 'INTERNAL USE ONLY',
      active_index: 0,
      labels: ['INTERNAL USE ONLY'],
      marks: ['X'],
      rendered: 'INTERNAL USE ONLY',
    };
  }
  if (mode === 'none') {
    return { mode, active: '', active_index: 0, labels: [], marks: [], rendered: '' };
  }
  const labels = mode === 'rule_55' ? RULE_55_LABELS : RULE_48_LABELS;
  const idx = COPY_INDEX[(copyType || 'ORIGINAL').toUpperCase()] ?? 0;
  const marks: string[] = [' ', ' ', ' '];
  marks[idx] = 'X';
  const rendered = labels.map((l, i) => `${l} (${marks[i]})`).join(' | ');
  return {
    mode,
    active: labels[idx],
    active_index: idx,
    labels: [...labels],
    marks,
    rendered,
  };
}

// ---------------------------------------------------------------------------
// Declarations & statutory footer (mirror backend canonical text)
// ---------------------------------------------------------------------------

const DECLARATIONS: Record<string, string> = {
  tax_invoice:
    'We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.',
  thermal_receipt:
    'Thank you for your purchase. Goods once sold are governed by our return policy displayed in-store.',
  credit_note:
    'Credit note issued in accordance with Section 34(1) of the CGST Act 2017. Output tax shall be adjusted in the GSTR-3B for the period.',
  debit_note:
    'Debit note issued in accordance with Section 34(3) of the CGST Act 2017. Output tax shall be reported in the GSTR-3B for the period.',
  grn:
    'Goods inspected and received in good condition unless variance / remarks recorded against any line.',
  rx_card:
    'This prescription is valid for use with any registered optician. The practitioner has not received any consideration for prescribing a particular brand of frame, lens, or contact lens.',
  job_card:
    'Lens specification verified against prescription on file. Issued for internal workshop use; not a customer-facing document.',
  z_report:
    'Day-end totals are verified against physical cash and tender splits. Variances over the policy threshold require manager sign-off.',
};

export function declarations(docType: string): string {
  return DECLARATIONS[String(docType || '').toLowerCase()] || '';
}

const FOOTERS: Record<string, string> = {
  tax_invoice:
    'Issued under Sec. 31 CGST Act 2017 r/w Rule 46. Retain for {retain} years per CGST Rule 56.',
  thermal_receipt:
    'Issued under Sec. 31 CGST Act 2017 r/w Rule 46. Retain for {retain} years per CGST Rule 56.',
  credit_note:
    'Issued under Sec. 34(1) CGST Act 2017 r/w Rule 53. Retain for {retain} years per CGST Rule 56.',
  debit_note:
    'Issued under Sec. 34(3) CGST Act 2017 r/w Rule 53. Retain for {retain} years per CGST Rule 56.',
  delivery_challan:
    'Issued under Rule 55 CGST Rules 2017. Retain for {retain} years per CGST Rule 56.',
  grn:
    'Goods Receipt Note - internal control document. Retain for {retain} years per CGST Rule 56.',
  z_report:
    'Day-end cash reconciliation (SOP-FIN-02). Retain for {retain} years per CGST Rule 56.',
  rx_card:
    'Issued under NCAHP Act 2021 by a registered allied healthcare professional. Valid for use with any registered optician.',
  job_card: 'Internal lens workshop record. Not a statutory tax document.',
};

export function statutoryFooter(docType: string, retainYears = 7): string {
  const tpl = FOOTERS[String(docType || '').toLowerCase()]
    || 'System-generated document. Retain for {retain} years per CGST Rule 56.';
  return tpl.replace('{retain}', String(retainYears || 7));
}

// ---------------------------------------------------------------------------
// Header builders (mirror backend LegalHeader / StaffHeader)
// ---------------------------------------------------------------------------

function pick(obj: Record<string, unknown> | null | undefined, ...keys: string[]): string {
  if (!obj) return '';
  for (const k of keys) {
    const v = obj[k];
    if (v === null || v === undefined) continue;
    const s = String(v).trim();
    if (s) return s;
  }
  return '';
}

function applyOverrides(defaults: Record<string, unknown>, overrides?: OverrideFields | null): Record<string, unknown> {
  const out = { ...defaults };
  if (!overrides) return out;
  for (const [k, v] of Object.entries(overrides)) {
    if (v === null || v === undefined) continue;
    if (typeof v === 'string' && !v.trim()) continue;
    out[k] = v;
  }
  return out;
}

function gstinForState(
  entity: EntityLike | null | undefined,
  stateCode: string
): { gstin: string; state_name: string } {
  if (!entity || !entity.gstins || !entity.gstins.length) return { gstin: '', state_name: '' };
  let primary = entity.gstins[0];
  for (const g of entity.gstins) {
    if (stateCode && String(g.state_code).trim() === stateCode) {
      return { gstin: g.gstin || '', state_name: g.state_name || '' };
    }
    if (g.is_primary) primary = g;
  }
  return { gstin: primary.gstin || '', state_name: primary.state_name || '' };
}

export function buildLegalHeader(
  entity: EntityLike | null | undefined,
  store: StoreLike | null | undefined,
  docType: string,
  opts: {
    docNumber?: string;
    docDate?: Date | string | null;
    placeOfSupply?: string;
    reverseCharge?: boolean;
    copyMarker?: string;
    overrides?: OverrideFields | null;
    extraMeta?: Array<[string, string]>;
    copyMarkerMode?: 'rule_48' | 'rule_55' | 'internal' | 'none';
  } = {}
): LegalHeaderData {
  const legalName = pick(entity as Record<string, unknown>, 'legal_name', 'name');
  let tradeName = pick(entity as Record<string, unknown>, 'name');
  if (tradeName === legalName) tradeName = '';

  const pan = pick(entity as Record<string, unknown>, 'pan');
  const cin = pick(entity as Record<string, unknown>, 'cin', 'llpin');
  const registeredAddress = pick(entity as Record<string, unknown>, 'registered_address');
  const registeredPhone = pick(entity as Record<string, unknown>, 'registered_phone');
  const registeredEmail = pick(entity as Record<string, unknown>, 'registered_email');
  const website = pick(entity as Record<string, unknown>, 'website');

  const storeName = pick(store as Record<string, unknown>, 'name', 'store_name', 'trade_name');
  const addrLines: string[] = [];
  for (const k of ['address', 'street', 'address_line_1']) {
    const v = pick(store as Record<string, unknown>, k);
    if (v && !addrLines.includes(v)) addrLines.push(v);
  }
  const city = pick(store as Record<string, unknown>, 'city');
  const stateNameStore = pick(store as Record<string, unknown>, 'state', 'state_name');
  const pincode = pick(store as Record<string, unknown>, 'pincode');
  const storePhone = pick(store as Record<string, unknown>, 'phone');
  const storeEmail = pick(store as Record<string, unknown>, 'email');
  const stateCode = pick(store as Record<string, unknown>, 'state_code');

  const storeAddrFull = [...addrLines, city, stateNameStore, pincode].filter(Boolean).join(', ');

  const { gstin, state_name: stateNameGst } = gstinForState(entity, stateCode);
  const stateName = stateNameGst || stateNameStore;

  const defaults: Record<string, unknown> = {
    legal_name: legalName,
    trade_name: tradeName,
    header_subtitle: '',
    registered_address: registeredAddress,
    registered_email: registeredEmail,
    registered_phone: registeredPhone,
    website,
    pan,
    cin,
    drug_licence_no: '',
    ncahp_uid: '',
    dmc_reg: '',
    signatory_name: '',
    signatory_designation: 'Authorised Signatory',
    footer_terms: '',
    logo_url: logoFromEntity(entity),
    retention_years: 7,
    reverse_charge_default: false,
  };
  const applied = applyOverrides(defaults, opts.overrides ?? null);

  let rc = !!opts.reverseCharge;
  if (!rc && applied.reverse_charge_default) rc = true;

  const placeStr = (opts.placeOfSupply ?? '').toString().trim() || (stateName || '');
  const cmb = copyMarker(
    opts.copyMarker || 'ORIGINAL',
    opts.copyMarkerMode || 'rule_48'
  );

  const meta: Array<[string, string]> = [];
  if (opts.docNumber) meta.push(['Document No.', opts.docNumber]);
  if (opts.docDate != null) meta.push(['Date', formatDate(opts.docDate)]);
  meta.push(['Place of Supply', placeStr]);
  meta.push(['Reverse Charge', rc ? 'Yes' : 'No']);
  if (opts.extraMeta) {
    for (const kv of opts.extraMeta) {
      if (!kv) continue;
      meta.push([String(kv[0] ?? ''), String(kv[1] ?? '')]);
    }
  }

  const kv: Array<[string, string]> = [
    ['Registered Office', String(applied.registered_address || '')],
    ['Place of Supply', storeAddrFull],
    ['Contact', [applied.registered_phone, applied.registered_email, applied.website].filter(Boolean).join(' | ')],
    ['GSTIN / UIN', gstin],
    ['State / Code', stateName ? `${stateName}${stateCode ? ' / ' + stateCode : ''}` : stateCode],
    ['PAN', String(applied.pan || '')],
    ['CIN', String(applied.cin || '')],
  ];
  if (applied.drug_licence_no) kv.push(['Drug Licence', String(applied.drug_licence_no)]);
  if (docType === 'rx_card') {
    if (applied.ncahp_uid) kv.push(['NCAHP UID', String(applied.ncahp_uid)]);
    if (applied.dmc_reg) kv.push(['State Council Reg', String(applied.dmc_reg)]);
  }

  return {
    doc_type: docType,
    doc_number: opts.docNumber || '',
    doc_date: opts.docDate ? formatDate(opts.docDate) : '',
    copy_marker: cmb,
    legal_name: String(applied.legal_name || ''),
    trade_name: String(applied.trade_name || ''),
    header_subtitle: String(applied.header_subtitle || ''),
    supplier_kv: kv.filter(([, v]) => v),
    store_name: storeName,
    store_address: storeAddrFull,
    store_phone: storePhone,
    store_email: storeEmail,
    place_of_supply: placeStr,
    state_code: stateCode,
    state_name: stateName,
    gstin,
    reverse_charge: rc,
    meta,
    signatory_name: String(applied.signatory_name || ''),
    signatory_designation: String(applied.signatory_designation || 'Authorised Signatory'),
    footer_terms: String(applied.footer_terms || ''),
    logo_url: String(applied.logo_url || ''),
    brand_label: brandLabelOf(store),
    retention_years: Number(applied.retention_years ?? 7),
  };
}

export function buildStaffHeader(
  entity: EntityLike | null | undefined,
  store: StoreLike | null | undefined,
  docType: string,
  opts: {
    docNumber?: string;
    docDate?: Date | string | null;
    overrides?: OverrideFields | null;
    extraMeta?: Array<[string, string]>;
  } = {}
): StaffHeaderData {
  const defaults: Record<string, unknown> = {
    trade_name: pick(entity as Record<string, unknown>, 'name'),
    header_subtitle: '',
    logo_url: logoFromEntity(entity),
    signatory_name: '',
    signatory_designation: '',
    footer_terms: '',
  };
  const applied = applyOverrides(defaults, opts.overrides ?? null);
  const storeName = pick(store as Record<string, unknown>, 'name', 'store_name', 'trade_name');
  const storeCode = pick(store as Record<string, unknown>, 'code', 'store_code');
  let branchLabel = [storeName, storeCode].filter(Boolean).join(', ');
  if (!branchLabel) branchLabel = pick(store as Record<string, unknown>, 'city');

  const meta: Array<[string, string]> = [];
  if (opts.docNumber) meta.push(['Document No.', opts.docNumber]);
  if (opts.docDate != null) meta.push(['Date', formatDate(opts.docDate)]);
  if (opts.extraMeta) {
    for (const kv of opts.extraMeta) {
      if (!kv) continue;
      meta.push([String(kv[0] ?? ''), String(kv[1] ?? '')]);
    }
  }

  return {
    doc_type: docType,
    doc_number: opts.docNumber || '',
    doc_date: opts.docDate ? formatDate(opts.docDate) : '',
    trade_name: String(applied.trade_name || ''),
    header_subtitle: String(applied.header_subtitle || ''),
    branch_label: branchLabel,
    logo_url: String(applied.logo_url || ''),
    brand_label: brandLabelOf(store),
    meta,
    signatory_name: String(applied.signatory_name || ''),
    signatory_designation: String(applied.signatory_designation || ''),
    footer_terms: String(applied.footer_terms || ''),
    copy_marker: copyMarker('ORIGINAL', 'internal'),
  };
}

// ---------------------------------------------------------------------------
// HSN-wise tax summary (mirror backend hsn_tax_summary)
// ---------------------------------------------------------------------------

export interface HsnSummaryRow {
  hsn: string;
  description: string;
  qty: number;
  taxable: number;
  rate: number;
  cgst: number;
  sgst: number;
  igst: number;
  total_tax: number;
  line_count: number;
}

export interface HsnSummary {
  interstate: boolean;
  rows: HsnSummaryRow[];
  totals: {
    taxable: number;
    cgst: number;
    sgst: number;
    igst: number;
    total_tax: number;
  };
}

interface HsnItem {
  hsn_code?: string;
  hsn?: string;
  taxable_value?: number;
  taxable?: number;
  amount?: number;
  gst_rate?: number;
  rate?: number;
  qty?: number;
  quantity?: number;
  description?: string;
  name?: string;
}

function stateCodeOf(value: unknown): string {
  if (value === null || value === undefined) return '';
  const s = String(value).trim().toUpperCase();
  if (!s) return '';
  if (s.length >= 2 && /^\d{2}/.test(s)) return s.substring(0, 2);
  const m = s.match(/\((\d{2})\)/);
  if (m) return m[1];
  return '';
}

export function hsnTaxSummary(
  items: HsnItem[],
  placeOfSupply?: unknown,
  supplierState?: unknown
): HsnSummary {
  const pos = stateCodeOf(placeOfSupply);
  const sup = stateCodeOf(supplierState);
  const interstate = !!pos && !!sup && pos !== sup;

  const rows: Record<string, HsnSummaryRow> = {};
  for (const raw of items || []) {
    if (!raw || typeof raw !== 'object') continue;
    const hsn = String(raw.hsn_code ?? raw.hsn ?? '').trim() || '-';
    const taxable = Number(
      raw.taxable_value ?? raw.taxable ?? raw.amount ?? 0
    ) || 0;
    const rate = Number(raw.gst_rate ?? raw.rate ?? 0) || 0;
    const qty = Number(raw.qty ?? raw.quantity ?? 0) || 0;
    const description = String(raw.description ?? raw.name ?? '').trim();

    const key = `${hsn}|${rate.toFixed(2)}`;
    if (!rows[key]) {
      rows[key] = {
        hsn,
        description,
        qty: 0,
        taxable: 0,
        rate: Math.round(rate * 100) / 100,
        cgst: 0,
        sgst: 0,
        igst: 0,
        total_tax: 0,
        line_count: 0,
      };
    }
    const row = rows[key];
    if (description && !row.description) row.description = description;
    row.qty += qty;
    row.taxable = Math.round((row.taxable + taxable) * 100) / 100;
    const tax = Math.round(taxable * rate) / 100;
    if (interstate) {
      row.igst = Math.round((row.igst + tax) * 100) / 100;
    } else {
      const half = Math.round((tax / 2) * 100) / 100;
      row.cgst = Math.round((row.cgst + half) * 100) / 100;
      row.sgst = Math.round((row.sgst + (tax - half)) * 100) / 100;
    }
    row.total_tax = Math.round((row.cgst + row.sgst + row.igst) * 100) / 100;
    row.line_count += 1;
  }

  const outRows = Object.values(rows).sort((a, b) =>
    a.hsn < b.hsn ? -1 : a.hsn > b.hsn ? 1 : a.rate - b.rate
  );
  const totals = {
    taxable: Math.round(outRows.reduce((s, r) => s + r.taxable, 0) * 100) / 100,
    cgst: Math.round(outRows.reduce((s, r) => s + r.cgst, 0) * 100) / 100,
    sgst: Math.round(outRows.reduce((s, r) => s + r.sgst, 0) * 100) / 100,
    igst: Math.round(outRows.reduce((s, r) => s + r.igst, 0) * 100) / 100,
    total_tax: Math.round(outRows.reduce((s, r) => s + r.total_tax, 0) * 100) / 100,
  };
  return { interstate, rows: outRows, totals };
}

// ---------------------------------------------------------------------------
// React components -- statutory header / footer renderers
// ---------------------------------------------------------------------------

// Inline styles -- statutory docs MUST render identically regardless of any
// app-level Tailwind purge / dark-mode token. Pure ink-on-white, sans-only.
const INK = '#1a1a19';
const INK_3 = '#4a4a45';
const INK_4 = '#7a7a72';
const INK_5 = '#aaa9a3';
const BG_SUNK = '#f6f5f0';

const kvLabelStyle: React.CSSProperties = {
  padding: '2px 8px 2px 0',
  fontSize: 9.5,
  color: INK_3,
  textTransform: 'uppercase',
  letterSpacing: '.08em',
  whiteSpace: 'nowrap',
  verticalAlign: 'top',
  lineHeight: 1.45,
  fontFamily:
    'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
};
const kvValStyle: React.CSSProperties = {
  padding: '2px 0',
  fontSize: 10.5,
  color: INK,
  verticalAlign: 'top',
  lineHeight: 1.45,
  fontFamily:
    'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
};
const eyebrowStyle: React.CSSProperties = {
  fontSize: 9,
  color: INK_3,
  textTransform: 'uppercase',
  letterSpacing: '.1em',
  fontWeight: 500,
};

// Bordered table cells the templates use for line items + HSN summary.
export const tblHead: React.CSSProperties = {
  padding: '7px 8px',
  fontSize: 9.5,
  fontWeight: 600,
  color: INK,
  textTransform: 'uppercase',
  letterSpacing: '.06em',
  background: BG_SUNK,
  border: `1px solid ${INK_4}`,
  textAlign: 'center',
};
export const tblCell: React.CSSProperties = {
  padding: '7px 8px',
  fontSize: 11,
  color: INK,
  border: `1px solid ${INK_5}`,
  textAlign: 'center',
  verticalAlign: 'top',
};
export const tblNum: React.CSSProperties = {
  ...tblCell,
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
  fontWeight: 500,
};

export function CopyMarkerBar({ docType, marker }: { docType: string; marker: CopyMarker }) {
  if (marker.mode === 'none') return null;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        alignItems: 'center',
        padding: '6px 18px',
        background: INK,
        color: '#fff',
        fontFamily:
          'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        fontSize: 10,
        letterSpacing: '.16em',
        fontWeight: 600,
        textTransform: 'uppercase',
      }}
    >
      <span>{docType}</span>
      <span style={{ opacity: 0.8, fontSize: 9.5 }}>{marker.rendered}</span>
    </div>
  );
}

export function LegalHeaderView({
  header,
  docTypeLabel,
}: {
  header: LegalHeaderData;
  docTypeLabel?: string;
}) {
  const label = docTypeLabel || header.doc_type.replace(/_/g, ' ').toUpperCase();
  return (
    <>
      <CopyMarkerBar docType={label} marker={header.copy_marker} />
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', borderBottom: `1.5px solid ${INK}` }}>
        <div style={{ padding: '12px 18px', borderRight: `1px solid ${INK_4}` }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            {header.logo_url ? (
              <img
                src={header.logo_url}
                alt="logo"
                style={{ maxWidth: 56, maxHeight: 40, objectFit: 'contain' }}
              />
            ) : (
              <div
                style={{
                  width: 34,
                  height: 34,
                  border: `1.5px solid ${INK}`,
                  display: 'grid',
                  placeItems: 'center',
                  fontFamily:
                    'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
                  fontSize: 18,
                  fontWeight: 700,
                  color: INK,
                }}
              >
                {(header.trade_name || header.legal_name || 'B').charAt(0).toUpperCase()}
              </div>
            )}
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 700, letterSpacing: '.01em', color: INK }}>
                {header.legal_name || header.trade_name}
              </div>
              {header.trade_name && header.legal_name && (
                <div style={{ fontSize: 9.5, color: INK_3, textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 1 }}>
                  Trade name: {header.trade_name}
                  {header.header_subtitle ? ' · ' + header.header_subtitle : ''}
                </div>
              )}
              {(!header.trade_name || !header.legal_name) && header.header_subtitle && (
                <div style={{ fontSize: 9.5, color: INK_3, textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 1 }}>
                  {header.header_subtitle}
                </div>
              )}
            </div>
          </div>
          <table style={{ marginTop: 8, fontSize: 10.5, borderCollapse: 'collapse' }}>
            <tbody>
              {header.supplier_kv.map(([k, v]) => (
                <tr key={k}>
                  <td style={kvLabelStyle}>{k}</td>
                  <td
                    style={{
                      ...kvValStyle,
                      fontFamily:
                        /GSTIN|PAN|CIN|Code|Reg/.test(k)
                          ? 'JetBrains Mono, Menlo, Consolas, monospace'
                          : kvValStyle.fontFamily,
                      fontWeight: /GSTIN|PAN/.test(k) ? 600 : 400,
                    }}
                  >
                    {v}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ padding: '12px 18px' }}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              borderBottom: `1px dashed ${INK_5}`,
              paddingBottom: 8,
              marginBottom: 8,
            }}
          >
            <div>
              <div style={eyebrowStyle}>Document No.</div>
              <div style={{ fontFamily: 'JetBrains Mono, Menlo, Consolas, monospace', fontSize: 15, fontWeight: 700, letterSpacing: '.02em', marginTop: 2 }}>
                {header.doc_number || '—'}
              </div>
            </div>
            <div>
              <div style={eyebrowStyle}>Date</div>
              <div style={{ fontFamily: 'JetBrains Mono, Menlo, Consolas, monospace', fontSize: 13, fontWeight: 600, marginTop: 2 }}>
                {header.doc_date || '—'}
              </div>
            </div>
          </div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
            <tbody>
              {header.meta.map(([k, v]) => (
                <tr key={k}>
                  <td style={kvLabelStyle}>{k}</td>
                  <td style={{ ...kvValStyle, textAlign: 'right' }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

export function StaffHeaderView({
  header,
  docTypeLabel,
}: {
  header: StaffHeaderData;
  docTypeLabel?: string;
}) {
  const label = docTypeLabel || header.doc_type.replace(/_/g, ' ').toUpperCase();
  return (
    <>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr auto',
          alignItems: 'center',
          padding: '5px 16px',
          background: INK,
          color: '#fff',
          fontSize: 9.5,
          letterSpacing: '.16em',
          fontWeight: 600,
          textTransform: 'uppercase',
        }}
      >
        <span>{label}</span>
        <span style={{ opacity: 0.7, fontSize: 9 }}>{header.copy_marker.rendered}</span>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr auto',
          padding: '8px 16px',
          borderBottom: `1.5px solid ${INK}`,
          gap: 14,
          alignItems: 'center',
        }}
      >
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {header.logo_url ? (
            <img
              src={header.logo_url}
              alt="logo"
              style={{ maxWidth: 48, maxHeight: 34, objectFit: 'contain' }}
            />
          ) : (
            <div
              style={{
                width: 30,
                height: 30,
                border: `1.5px solid ${INK}`,
                display: 'grid',
                placeItems: 'center',
                fontWeight: 700,
                fontSize: 16,
                color: INK,
              }}
            >
              {(header.trade_name || 'B').charAt(0).toUpperCase()}
            </div>
          )}
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.01em', color: INK }}>{header.trade_name || '—'}</div>
            <div style={{ fontSize: 9, color: INK_3, textTransform: 'uppercase', letterSpacing: '.1em', marginTop: 1 }}>
              {header.branch_label}
              {header.header_subtitle ? ' · ' + header.header_subtitle : ''}
            </div>
          </div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ ...eyebrowStyle, fontSize: 8.5 }}>Document No.</div>
          <div
            style={{
              fontFamily: 'JetBrains Mono, Menlo, Consolas, monospace',
              fontSize: 14,
              fontWeight: 700,
              marginTop: 1,
              letterSpacing: '.02em',
              color: INK,
            }}
          >
            {header.doc_number || '—'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {header.meta.map(([k, v]) => (
            <div
              key={k}
              style={{
                display: 'inline-flex',
                alignItems: 'baseline',
                gap: 6,
                padding: '3px 8px',
                border: `1px solid ${INK_5}`,
                fontSize: 9.5,
                color: INK,
              }}
            >
              <span style={{ color: INK_4, textTransform: 'uppercase', letterSpacing: '.08em' }}>{k}</span>
              <span style={{ fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// HSN-wise tax summary table -- used by Tax Invoice, GRN, Z-Report.
export function HsnSummaryTable({ summary }: { summary: HsnSummary }) {
  const { interstate, rows, totals } = summary;
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4 }}>
      <thead>
        <tr>
          <th style={tblHead}>HSN/SAC</th>
          <th style={{ ...tblHead, textAlign: 'left' }}>Description</th>
          <th style={tblHead}>Taxable Value</th>
          <th style={tblHead}>Rate</th>
          {interstate ? (
            <th style={tblHead}>IGST</th>
          ) : (
            <>
              <th style={tblHead}>CGST</th>
              <th style={tblHead}>SGST</th>
            </>
          )}
          <th style={tblHead}>Total Tax</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.hsn}-${r.rate}-${i}`}>
            <td style={{ ...tblCell, fontFamily: 'JetBrains Mono, Menlo, Consolas, monospace' }}>{r.hsn}</td>
            <td style={{ ...tblCell, textAlign: 'left' }}>{r.description || '—'}</td>
            <td style={tblNum}>{inr(r.taxable, { withPaise: true })}</td>
            <td style={tblNum}>{r.rate.toFixed(2)}%</td>
            {interstate ? (
              <td style={tblNum}>{inr(r.igst, { withPaise: true })}</td>
            ) : (
              <>
                <td style={tblNum}>{inr(r.cgst, { withPaise: true })}</td>
                <td style={tblNum}>{inr(r.sgst, { withPaise: true })}</td>
              </>
            )}
            <td style={tblNum}>{inr(r.total_tax, { withPaise: true })}</td>
          </tr>
        ))}
        <tr>
          <td colSpan={2} style={{ ...tblCell, textAlign: 'right', fontWeight: 700 }}>TOTAL</td>
          <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totals.taxable, { withPaise: true })}</td>
          <td style={tblCell}></td>
          {interstate ? (
            <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totals.igst, { withPaise: true })}</td>
          ) : (
            <>
              <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totals.cgst, { withPaise: true })}</td>
              <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totals.sgst, { withPaise: true })}</td>
            </>
          )}
          <td style={{ ...tblNum, fontWeight: 700 }}>{inr(totals.total_tax, { withPaise: true })}</td>
        </tr>
      </tbody>
    </table>
  );
}

// Footer block: amount-in-words, declaration, signatory, statutory line.
export function LegalFooterBlock({
  header,
  amountInWordsText,
  declarationText,
  showAmountInWords = true,
  showSignatoryBlock = true,
  signLabel,
  extra,
}: {
  header: LegalHeaderData;
  amountInWordsText?: string;
  declarationText?: string;
  showAmountInWords?: boolean;
  showSignatoryBlock?: boolean;
  signLabel?: string;
  extra?: ReactNode;
}) {
  const footerLine = statutoryFooter(header.doc_type, header.retention_years || 7);
  return (
    <>
      {(showAmountInWords && amountInWordsText) || declarationText || header.footer_terms ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: `1px solid ${INK_5}`, borderTop: `1px solid ${INK_5}` }}>
          <div style={{ padding: '10px 18px', borderRight: `1px solid ${INK_5}` }}>
            {showAmountInWords && amountInWordsText && (
              <div style={{ marginBottom: declarationText || header.footer_terms ? 10 : 0 }}>
                <div style={eyebrowStyle}>Amount chargeable (in words)</div>
                <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 3, lineHeight: 1.45, color: INK }}>{amountInWordsText}</div>
              </div>
            )}
            {declarationText && (
              <div style={{ marginBottom: header.footer_terms ? 10 : 0 }}>
                <div style={eyebrowStyle}>Declaration</div>
                <div style={{ fontSize: 10, color: INK_3, marginTop: 3, lineHeight: 1.55 }}>{declarationText}</div>
              </div>
            )}
            {header.footer_terms && (
              <div>
                <div style={eyebrowStyle}>Terms</div>
                <div style={{ fontSize: 10, color: INK_3, marginTop: 3, lineHeight: 1.55 }}>{header.footer_terms}</div>
              </div>
            )}
            {extra}
          </div>
          <div style={{ padding: '10px 18px' }}>
            {showSignatoryBlock && (
              <div>
                <div style={eyebrowStyle}>{signLabel || ('For ' + (header.legal_name || header.trade_name || ''))}</div>
                <div style={{ height: 42, marginTop: 4, marginBottom: 2, borderBottom: `0.5px solid ${INK_5}` }} />
                <div style={{ fontSize: 10, color: INK_3, display: 'flex', justifyContent: 'space-between' }}>
                  <span>
                    {header.signatory_name || 'Authorised signatory'}
                    {header.signatory_name && header.signatory_designation ? ' · ' + header.signatory_designation : ''}
                  </span>
                  <span style={{ color: INK_5 }}>[Seal]</span>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}
      <div
        style={{
          padding: '7px 18px',
          fontSize: 9,
          color: INK_4,
          textTransform: 'uppercase',
          letterSpacing: '.08em',
          textAlign: 'center',
        }}
      >
        {footerLine} · E. & O. E. · system-generated
      </div>
    </>
  );
}

// Convenience -- escape the raw glyphs used in the footer (TypeScript strings
// embed them directly, but exporting a constant makes them harder to mistype).
export const SYSTEM_GENERATED_TAG = 'system-generated';

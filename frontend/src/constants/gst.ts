// ============================================================================
// IMS 2.0 - GST Constants and Utilities
// ============================================================================
// HSN codes, tax rates, and GST utilities for Indian optical retail
// Updated to GST 2.0 (effective September 22, 2025) — 12% slab eliminated
// Ref: 56th GST Council Meeting, CBIC notifications

export interface HSNCode {
  code: string;
  description: string;
  category: 'LENS' | 'FRAME' | 'SPECTACLE' | 'CONTACT_LENS' | 'SUNGLASSES' | 'ACCESSORIES' | 'WATCH' | 'SMARTWATCH' | 'CLOCK' | 'HEARING_AID' | 'SERVICE';
}

// ============================================================================
// GST 2.0 Rate Structure (Sep 22, 2025):
//   0% — Life-saving medical equipment
//   5% — Essential: corrective lenses, frames, spectacles, contact lenses
//  18% — Standard: sunglasses, watches, accessories, services
//  40% — Luxury/sin goods (not applicable to optical)
// ============================================================================

// HSN Codes for Optical & Lifestyle Products.
//
// CODE + DESCRIPTION ONLY -- deliberately NO RATE. The rate a code carries is
// the SERVER's answer (services/gst_rates.resolve_gst_rate_strict, over the
// owner-editable hsn_gst_master), and it is applied where it matters: the
// cataloguing door derives a product's stored gst_rate from its HSN, and the
// purchase side resolves the same way off the same HSN. A rate column here
// would be a second copy of that rule in a place that cannot be told when the
// rule changes -- which is exactly how this table came to state 5% on HSN
// 900319 while the server refused to state anything at all.
//
// So: this list names the codes a cataloguer may pick. It does not price them.
export const HSN_CODES: Record<string, HSNCode> = {
  // Chapter 90: Optical instruments
  '900130': {
    code: '900130',
    description: 'Contact lenses',
    category: 'CONTACT_LENS',
  },
  '900140': {
    code: '900140',
    description: 'Spectacle lenses of glass',
    category: 'LENS',
  },
  '900150': {
    code: '900150',
    description: 'Spectacle lenses of other materials (CR-39, polycarbonate, hi-index)',
    category: 'LENS',
  },
  '900311': {
    code: '900311',
    description: 'Frames of plastics for spectacles',
    category: 'FRAME',
  },
  '900319': {
    code: '900319',
    description: 'Frames of other materials (metal, titanium, wood)',
    category: 'FRAME',
  },
  '900410': {
    code: '900410',
    description: 'Sunglasses',
    category: 'SUNGLASSES',
  },
  '900490': {
    code: '900490',
    description: 'Corrective spectacles, goggles and the like',
    category: 'SPECTACLE',
  },
  // Chapter 91: Watches
  '910111': {
    code: '910111',
    description: 'Wrist watches with mechanical display',
    category: 'WATCH',
  },
  '910221': {
    code: '910221',
    description: 'Wrist watches, smart watches (electronic)',
    category: 'SMARTWATCH',
  },
  '910500': {
    code: '910500',
    description: 'Clocks (wall clocks, alarm clocks)',
    category: 'CLOCK',
  },
  // Chapter 90: Hearing aids (NIL / exempt for complete devices; parts 18%)
  '902140': {
    code: '902140',
    description: 'Hearing aids (complete device) — NIL/exempt; parts attract 18%',
    category: 'HEARING_AID',
  },
  // Accessories & Services
  '392690': {
    code: '392690',
    description: 'Spectacle cases, cleaning cloths, accessories (plastics)',
    category: 'ACCESSORIES',
  },
  '998599': {
    code: '998599',
    description: 'Optical services (fitting, repair, adjustment)',
    category: 'SERVICE',
  },
};


// ============================================================================
// Category -> (HSN code, GST rate)
// ============================================================================
// ONE table. Until 2026-08-27 this was TWO switches -- one answering the rate,
// one answering the HSN -- each carrying the same forty category spellings in a
// different order. Two lists of the same thing drift, and this pair did: the
// live picker code `CCL` (Colour Contact Lens) was in NEITHER, so the screen
// fell through to both defaults, showed HSN 900490 at 18%, and the server then
// stored 5% off that very HSN. Screen and stored contradicted each other on a
// category this business bills every day.
//
// The rate here is only ever a PREVIEW. The stored rate is derived server-side
// from the product's HSN (services/gst_rates.resolve_gst_rate_strict), so each
// entry below is written so a category's rate IS the rate its own HSN carries.
// __tests__/categoryTax.test.ts checks that, entry by entry, and checks that
// every code the Add-Product picker offers is in here at all. The rate is also
// checked against the SERVER's own resolver, across languages, by
// backend/tests/test_category_tax_matches_server.py -- which reads this very
// file, so there is no third hand-copied list to drift.
//
// Accepts the canonical schema enum (FRAME, OPTICAL_LENS, ...), the seed
// plural/alt forms (FRAMES, RX_LENSES, ...), and the short UI codes used by the
// Add-Product picker (FR, LS, CL, CCL, RG, SG, WT, CK, HA, ACC, SMT*), so master
// == billing regardless of which vocabulary a row was written in.
interface CategoryTax {
  /** 6-digit HSN, owner 2026-07-05 (the 4-digit variant was removed app-wide). */
  hsn: string;
  /** GST 2.0 percent -- must equal what `hsn` settles on the server. */
  rate: number;
}

const CATEGORY_TAX = {
  // Frames -> 9003 -> 5%
  FRAME: { hsn: '900311', rate: 5 },
  FRAMES: { hsn: '900311', rate: 5 },
  EYEGLASS_FRAME: { hsn: '900311', rate: 5 },
  FR: { hsn: '900311', rate: 5 },
  // Spectacle / optical lenses -> 9001 -> 5%
  LENS: { hsn: '900150', rate: 5 },
  RX_LENSES: { hsn: '900150', rate: 5 },
  OPTICAL_LENS: { hsn: '900150', rate: 5 },
  EYEGLASS_LENS: { hsn: '900150', rate: 5 },
  LS: { hsn: '900150', rate: 5 },
  // Contact lenses, clear AND coloured -> 9001 -> 5%. CCL is the owner's
  // 2026-07-05 split of colour/cosmetic lenses into their own picker code
  // (canonical COLORED_CONTACT_LENS); the server prices it at 900130/5%
  // (services/gst_rates.GST_CATEGORY_TABLE), and so does the screen.
  CONTACT_LENS: { hsn: '900130', rate: 5 },
  CONTACT_LENSES: { hsn: '900130', rate: 5 },
  COLORED_CONTACT_LENS: { hsn: '900130', rate: 5 },
  COLOUR_CONTACTS: { hsn: '900130', rate: 5 },
  CL: { hsn: '900130', rate: 5 },
  CCL: { hsn: '900130', rate: 5 },
  // Corrective spectacles + readymade readers -> 9004 -> 5%
  SPECTACLE: { hsn: '900490', rate: 5 },
  COMPLETE_SPECTACLE: { hsn: '900490', rate: 5 },
  READING_GLASSES: { hsn: '900490', rate: 5 },
  RG: { hsn: '900490', rate: 5 },
  // Non-corrective sunglasses -> 9004 -> 18% (NOT in the GST 2.0 reduction)
  SUNGLASSES: { hsn: '900410', rate: 18 },
  SUNGLASS: { hsn: '900410', rate: 18 },
  SG: { hsn: '900410', rate: 18 },
  // Watches / smart watches / clocks -> 9101 / 9102 / 9105 -> 18%
  WRIST_WATCHES: { hsn: '910111', rate: 18 },
  WATCH: { hsn: '910111', rate: 18 },
  WT: { hsn: '910111', rate: 18 },
  SMARTWATCHES: { hsn: '910221', rate: 18 },
  SMARTWATCH: { hsn: '910221', rate: 18 },
  SMTWT: { hsn: '910221', rate: 18 },
  WALL_CLOCK: { hsn: '910500', rate: 18 },
  WALL_CLOCKS: { hsn: '910500', rate: 18 },
  CLOCK: { hsn: '910500', rate: 18 },
  CK: { hsn: '910500', rate: 18 },
  // Smartglasses (electronic eyewear) -> 18%, owner-confirmed 2026-06-17.
  // GST-REVIEW (unchanged, pre-dates this table): the server's own category
  // default for SMTSG/SMTFR is HSN 852580, not 900410. The RATE agrees at 18%
  // either way, which is why the screen and the server do not contradict each
  // other here; the CODE does not, and moving it is a data decision (35 live
  // products already carry 852580) rather than a rename.
  SMARTGLASSES: { hsn: '900410', rate: 18 },
  SMTSG: { hsn: '900410', rate: 18 },
  SMTFR: { hsn: '900410', rate: 18 },
  // Hearing aids -> 9021 -> NIL/exempt for complete devices (parts are 18%)
  HEARING_AID: { hsn: '902140', rate: 0 },
  HEARING_AIDS: { hsn: '902140', rate: 0 },
  HA: { hsn: '902140', rate: 0 },
  // Accessories & services -> 18%
  ACCESSORIES: { hsn: '392690', rate: 18 },
  ACC: { hsn: '392690', rate: 18 },
  SERVICE: { hsn: '998599', rate: 18 },
  SERVICES: { hsn: '998599', rate: 18 },
} as const satisfies Record<string, CategoryTax>;

/** Every category spelling this table prices.
 *
 *  The Add-Product picker's list (`pages/catalog/productAddShared.CATEGORIES`)
 *  is typed against this, so a category the screen can offer CANNOT fall
 *  through to the unknown-category defaults below -- adding one without pricing
 *  it here fails `tsc` instead of silently quoting 18% under helper text that
 *  promises the HSN settles it. */
export type TaxedCategory = keyof typeof CATEGORY_TAX;

function categoryTax(category?: string | null): CategoryTax | undefined {
  const key = (category ?? '').toString().trim().toUpperCase();
  return (CATEGORY_TAX as Record<string, CategoryTax | undefined>)[key];
}

// Category -> GST rate (used by POS for a quick preview; the server bills).
export function getGSTRateByCategory(category: string): number {
  return categoryTax(category)?.rate ?? 18; // Conservative default
}

// Category -> HSN code.
export function getHSNByCategory(category: string): HSNCode | null {
  const tax = categoryTax(category);
  // Default to corrective spectacles for a spelling nothing recognises (legacy
  // rows, free text). A PICKER category can never land here -- see TaxedCategory.
  return HSN_CODES[tax ? tax.hsn : '900490'] ?? null;
}

// Calculate GST components
export function calculateGST(amount: number, gstRate: number) {
  const gstAmount = (amount * gstRate) / (100 + gstRate);
  // Round CGST down, assign remainder to SGST to avoid 1-paisa loss on odd amounts
  const roundedGst = parseFloat(gstAmount.toFixed(2));
  const cgst = Math.floor(roundedGst * 100 / 2) / 100;
  const sgst = parseFloat((roundedGst - cgst).toFixed(2));
  const baseAmount = amount - gstAmount;

  return {
    baseAmount: parseFloat(baseAmount.toFixed(2)),
    cgst,
    sgst,
    igst: 0, // For intra-state transactions
    totalGst: parseFloat(gstAmount.toFixed(2)),
    totalAmount: parseFloat(amount.toFixed(2)),
  };
}

// Calculate GST for inter-state transactions
export function calculateIGST(amount: number, gstRate: number) {
  const gstAmount = (amount * gstRate) / (100 + gstRate);
  const baseAmount = amount - gstAmount;

  return {
    baseAmount: parseFloat(baseAmount.toFixed(2)),
    cgst: 0,
    sgst: 0,
    igst: parseFloat(gstAmount.toFixed(2)),
    totalGst: parseFloat(gstAmount.toFixed(2)),
    totalAmount: parseFloat(amount.toFixed(2)),
  };
}

// Validate GST number format
export function validateGSTNumber(gstin: string): boolean {
  // GSTIN format: 2 digits (state) + 10 chars (PAN) + 1 char (entity) + 1 char (Z by default) + 1 check digit
  const gstRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
  return gstRegex.test(gstin);
}

// Get all HSN codes as dropdown options. 6-digit only (owner 2026-07-05):
// the 4-digit "turnover <= 5 Cr" simplification was removed app-wide.
// No rate in the label: the option names a code, the server prices it.
export function getHSNOptions(): Array<{ value: string; label: string }> {
  return Object.values(HSN_CODES).map((hsn) => ({
    value: hsn.code,
    label: `${hsn.code} - ${hsn.description}`,
  }));
}

// GSTR-1 Report Categories
export const GSTR1_SECTIONS = {
  B2B: 'B2B - Business to Business (Invoice-wise)',
  B2CL: 'B2CL - Business to Consumer Large (Invoice value > ₹2.5 lakh)',
  B2CS: 'B2CS - Business to Consumer Small (Invoice value <= ₹2.5 lakh)',
  CDNR: 'CDNR - Credit/Debit Notes (Registered)',
  CDNUR: 'CDNUR - Credit/Debit Notes (Unregistered)',
  EXP: 'EXP - Exports',
  NIL: 'NIL - Nil Rated, Exempted, Non-GST supplies',
};

// GSTR-3B Report Fields
export const GSTR3B_TABLES = {
  TABLE_3_1: 'Outward taxable supplies (other than zero rated, nil rated and exempted)',
  TABLE_3_2: 'Outward taxable supplies (zero rated)',
  TABLE_4: 'Eligible ITC',
  TABLE_5: 'Values of exempt, nil-rated and non-GST inward supplies',
  TABLE_6_1: 'Payment of tax',
};

export default {
  HSN_CODES,
  getHSNByCategory,
  calculateGST,
  calculateIGST,
  validateGSTNumber,
  getHSNOptions,
  GSTR1_SECTIONS,
  GSTR3B_TABLES,
};

// ============================================================================
// PLACE OF SUPPLY — inter-state (IGST) vs intra-state (CGST + SGST)
// ============================================================================
// India: a supply INSIDE one state is split half-and-half into CGST + SGST; a
// supply ACROSS states is a single IGST charge. Which one applies is decided by
// comparing the two parties' states -- for a SALE, the store's vs the
// customer's; for a PURCHASE, the vendor's vs the receiving store's. This
// business runs 3 legal entities over 4 GSTINs in 2 states, so neither side is
// a constant.
//
// A GSTIN carries its state in its first two digits, so two GSTINs settle it
// exactly. Falling back to the declared state name is a best effort. These are
// SCREEN previews: the server recomputes the split from the GST numbers on file
// and its answer is the one that is stored.

/** The 2-digit GST state code out of the first usable candidate (a 15-char
 *  GSTIN, or an already-bare 2-digit code). '' when none resolves. */
export function gstStateCode(...candidates: Array<string | null | undefined>): string {
  for (const candidate of candidates) {
    const s = (candidate ?? '').toString().trim();
    if (!s) continue;
    if (s.length === 15 && /^\d{2}/.test(s)) return s.slice(0, 2);
    if (/^\d{2}$/.test(s)) return s;
  }
  return '';
}

/** true = inter-state (IGST), false = intra-state (CGST + SGST),
 *  null = cannot be told from what we hold (caller must say so, not guess).
 *
 *  `null` is deliberately NOT `false`. A falsy unknown renders as
 *  "Same state - CGST + SGST" on every vendor whose GST number is missing,
 *  which is a wrong TAX LABEL stated with confidence, not a blank.
 *
 *  Only the two GST NUMBERS decide. This mirrors the server exactly --
 *  purchase_invoice_engine.determine_place_of_supply reads the supplier and
 *  recipient GSTINs and nothing else -- so the preview on screen and the split
 *  that actually gets stored can never contradict each other. A party's
 *  declared `state` is accepted (callers hand over the whole vendor/shop) but
 *  does NOT decide: an address is not a registration, and the tax turns on the
 *  registration. With no GSTIN the answer is "cannot tell", and the totals box
 *  says which assumption it is showing. */
export function isInterStateSupply(
  a: { gstin?: string | null; state?: string | null },
  b: { gstin?: string | null; state?: string | null },
): boolean | null {
  const codeA = gstStateCode(a.gstin);
  const codeB = gstStateCode(b.gstin);
  if (codeA && codeB) return codeA !== codeB;
  return null;
}

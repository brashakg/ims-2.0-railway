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
// Category -> (HSN code, GST rate): the picker REGISTRY and its tripwire
// ============================================================================
// This table prices NOTHING at runtime. Rates shown or billed come from the
// server (gstRuntime reads GET /products/gst-rates; the offline last resort is
// getGSTRateByCategory below). It exists for two jobs the runtime path cannot
// do:
//   1. COMPILE-TIME completeness -- productAddShared.CATEGORIES is typed
//      against TaxedCategory, so adding a picker category without declaring
//      its HSN + rate here fails `tsc`, naming it, instead of silently
//      falling through to a default (CCL did exactly that: 18% on screen,
//      5% stored, on a category these shops bill every day).
//   2. A DIFFERENTIAL TRIPWIRE -- backend/tests/test_category_tax_matches_server.py
//      reads this very file and prices every entry through the server's own
//      resolver, so this declaration cannot drift from what the server bills
//      without a red test. It is compared against the server, never consulted
//      instead of it.
interface CategoryTax {
  /** 6-digit HSN, owner 2026-07-05 (the 4-digit variant was removed app-wide). */
  hsn: string;
  /** GST 2.0 percent -- must equal what `hsn` settles on the server. */
  rate: number;
}

export const CATEGORY_TAX = {
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

// Category → GST Rate — THE ONE LOCAL GST TABLE, and the only one left
// ============================================================================
// Its single caller is gstRuntime.resolveGstRate(), as the LAST resort: a
// category GET /products/gst-rates does not name, or any category at all before
// that endpoint has answered. Everything else that used to be mirrored here
// (the category → HSN map, the category → master-row hint, the canonical
// category → rate table) is now read off the server — see gstRuntime.ts.
//
// It stays hand-written on purpose: it sits on the POS billing path, so what it
// returns offline must keep matching what it returned yesterday. Every category
// a product can be stored with is pinned, in BOTH states, by
// __tests__/gstServerFed.test.ts.
//
// If you change a rate here, you are changing what a cashier sees before the
// server answers. The rate the customer is actually billed comes from the
// backend (services/gst_rates.py), which recomputes every order.
export function getGSTRateByCategory(category: string): number {
  // Accepts the canonical schema enum (FRAME, OPTICAL_LENS, ...), the seed
  // plural/alt forms (FRAMES, RX_LENSES, ...), AND the short UI codes used on
  // AddProductPage (FR, LS, CL, RG, SG, WT, CK, HA, ACC, SMT*). All resolve
  // to the same GST 2.0 rate so master == billing regardless of vocabulary.
  switch (category?.toUpperCase()) {
    // Frames -> HSN 9003 -> 5%
    case 'FRAMES':
    case 'FRAME':
    case 'FR':
    case 'EYEGLASS_FRAME':
      return 5;
    // Spectacle / optical lenses + readymade reading glasses -> HSN 9001/9004 -> 5%
    case 'RX_LENSES':
    case 'LENS':
    case 'LS':
    case 'EYEGLASS_LENS':
    case 'OPTICAL_LENS':
    case 'READING_GLASSES':
    case 'RG':
      return 5;
    // Contact lenses (incl. coloured) -> HSN 9001 -> 5%
    case 'CONTACT_LENSES':
    case 'CONTACT_LENS':
    case 'CL':
    case 'COLOUR_CONTACTS':
    case 'COLORED_CONTACT_LENS':
      return 5;
    // Corrective spectacles -> HSN 9004 -> 5%
    case 'SPECTACLE':
    case 'COMPLETE_SPECTACLE':
      return 5;
    // Non-corrective sunglasses -> HSN 9004 -> 18%
    case 'SUNGLASSES':
    case 'SUNGLASS':
    case 'SG':
      return 18;
    // Watches / clocks / smartwatches -> HSN 9101/9102/9105 -> 18%
    case 'WRIST_WATCHES':
    case 'WATCH':
    case 'WT':
    case 'WALL_CLOCK':
    case 'WALL_CLOCKS':
    case 'CK':
    case 'SMARTWATCHES':
    case 'SMARTWATCH':
    case 'SMTWT':
      return 18;
    // Smartglasses (electronic eyewear, HSN 8525.80) -> 18%.
    // Owner-confirmed 2026-06-17 (Ch. 85 standard rate, not the 5% optical rate).
    case 'SMARTGLASSES':
    case 'SMTSG':
    case 'SMTFR':
      return 18;
    // Hearing aids -> HSN 9021 -> NIL/exempt (complete devices). Parts are 18%.
    case 'HEARING_AID':
    case 'HEARING_AIDS':
    case 'HA':
      return 0;
    case 'ACCESSORIES':
    case 'ACC':
    case 'SERVICE':
    case 'SERVICES':
      return 18;
    // Eye test / optometry consult -> EXEMPT health service, 0% (SAC 9993,
    // Notification 12/2017-CT(R) Sr. 74). These are real, printable order
    // lines (orders.py _NON_SERIALIZED_ITEM_TYPES). This table had no row for
    // one until 2026-08-27, so an eye test quoted the unknown-category rate on
    // a tax invoice while the customer was charged nothing.
    case 'EYE_TEST':
    case 'EYE_EXAM':
    case 'EYE_CHECKUP':
    case 'CONSULT':
    case 'CONSULTATION':
    case 'OPTOMETRY':
      return 0;
    default:
      // An UNRECOGNISED category -- a legacy row, a typo, a blank. 5%, because
      // that is what the server bills such a line (gst_rates.DEFAULT_GST_RATE,
      // moved 18 -> 5 on 2026-05-28 after a QA finding that an uncategorised
      // product was charged 18%). This side said 18 until 2026-08-27 and was
      // simply never reached: the deleted getHSNByCategory handed every caller
      // HSN 900490, whose master row answers 5, before this line could run.
      // With that copy gone the branch went live, and a frontend default that
      // contradicts the server on a BILLING DOCUMENT is a decision, not a
      // shrug -- over-charging GST is a customer-trust and a compliance
      // problem, so the unknown case biases to the dominant optical rate on
      // both sides.
      return 5;
  }
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

// The first two digits of a GSTIN ARE the state code. '' when there aren't two
// leading digits to read. The authoritative check (valid state code + GSTN
// check digit) is the server's -- api/services/org_validation.validate_gstin --
// this is only enough to show the state back while the user is typing.
export function gstinStateCode(gstin?: string | null): string {
  const s = (gstin || '').trim().toUpperCase();
  return /^[0-9]{2}/.test(s) ? s.slice(0, 2) : '';
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
  calculateGST,
  calculateIGST,
  validateGSTNumber,
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

/** The 2-digit DIGIT PREFIX out of the first usable candidate (a 15-char
 *  GSTIN, or an already-bare 2-digit code). '' when none resolves. This is a
 *  raw read, NOT a verdict that the prefix names a real Indian state -- "88"
 *  comes back "88". Only the server's state list can settle that, which is
 *  why isInterStateSupply demands one. */
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
 *  Only the two GST NUMBERS decide HERE. The server is close but NOT
 *  identical: purchase_invoice_engine.determine_place_of_supply takes a THIRD
 *  input -- the receiving shop's declared state (vendors._po_gst_parties
 *  passes it) -- which it weighs alongside the GSTINs. So a shop whose
 *  registration state and declared state DIFFER can make this preview and the
 *  stored split disagree. No live store is in that shape today (every store's
 *  declared state matches its GSTIN prefix), and which side should win --
 *  the registration or the shop the goods land in -- is an owner decision on
 *  file (bill-follows-store); until it lands, this preview reads
 *  registrations only. A party's declared `state` is accepted (callers hand
 *  over the whole vendor/shop) but does NOT decide on screen: an address is
 *  not a registration. With no GSTIN the answer is "cannot tell", and the
 *  totals box says which assumption it is showing.
 *
 *  `knownStates` is the server-fed code list (useGstStateCodes). A two-digit
 *  prefix the server does not list is not a state: the engine's parser
 *  (org_validation, behind determine_place_of_supply) reads NO state off such
 *  a GSTIN, so a screen that kept the raw digits would answer "IGST" (88 !=
 *  20) on a purchase the engine books intra-state. FAIL-CLOSED on purpose:
 *  until the list arrives -- and for the whole session if the meta endpoint is
 *  down -- the answer is null ("cannot tell"), never a tax verdict off an
 *  unverified prefix. */
export function isInterStateSupply(
  a: { gstin?: string | null; state?: string | null },
  b: { gstin?: string | null; state?: string | null },
  knownStates: Record<string, string>,
): boolean | null {
  const codeA = gstStateCode(a.gstin);
  const codeB = gstStateCode(b.gstin);
  if (codeA && codeB && knownStates[codeA] && knownStates[codeB]) {
    return codeA !== codeB;
  }
  return null;
}

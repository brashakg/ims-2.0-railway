// ============================================================================
// IMS 2.0 - GST Constants and Utilities
// ============================================================================
// HSN codes, tax rates, and GST utilities for Indian optical retail
// Updated to GST 2.0 (effective September 22, 2025) — 12% slab eliminated
// Ref: 56th GST Council Meeting, CBIC notifications

export interface HSNCode {
  code: string;
  description: string;
  gstRate: number;
  category: 'LENS' | 'FRAME' | 'SPECTACLE' | 'CONTACT_LENS' | 'SUNGLASSES' | 'ACCESSORIES' | 'WATCH' | 'SMARTWATCH' | 'CLOCK' | 'HEARING_AID' | 'SERVICE';
}

// ============================================================================
// GST 2.0 Rate Structure (Sep 22, 2025):
//   0% — Life-saving medical equipment
//   5% — Essential: corrective lenses, frames, spectacles, contact lenses
//  18% — Standard: sunglasses, watches, accessories, services
//  40% — Luxury/sin goods (not applicable to optical)
// ============================================================================

// HSN Codes for Optical & Lifestyle Products (GST 2.0)
export const HSN_CODES: Record<string, HSNCode> = {
  // Chapter 90: Optical instruments
  '900130': {
    code: '900130',
    description: 'Contact lenses',
    gstRate: 5,    // GST 2.0: reduced from 12%
    category: 'CONTACT_LENS',
  },
  '900140': {
    code: '900140',
    description: 'Spectacle lenses of glass',
    gstRate: 5,    // GST 2.0: reduced from 12%
    category: 'LENS',
  },
  '900150': {
    code: '900150',
    description: 'Spectacle lenses of other materials (CR-39, polycarbonate, hi-index)',
    gstRate: 5,    // GST 2.0: reduced from 12%
    category: 'LENS',
  },
  '900311': {
    code: '900311',
    description: 'Frames of plastics for spectacles',
    gstRate: 5,    // GST 2.0: reduced from 18%
    category: 'FRAME',
  },
  '900319': {
    code: '900319',
    description: 'Frames of other materials (metal, titanium, wood)',
    gstRate: 5,    // GST 2.0: reduced from 18%
    category: 'FRAME',
  },
  '900410': {
    code: '900410',
    description: 'Sunglasses',
    gstRate: 18,   // Unchanged — non-corrective eyewear
    category: 'SUNGLASSES',
  },
  '900490': {
    code: '900490',
    description: 'Corrective spectacles, goggles and the like',
    gstRate: 5,    // GST 2.0: reduced from 12%
    category: 'SPECTACLE',
  },
  // Chapter 91: Watches
  '910111': {
    code: '910111',
    description: 'Wrist watches with mechanical display',
    gstRate: 18,
    category: 'WATCH',
  },
  '910221': {
    code: '910221',
    description: 'Wrist watches, smart watches (electronic)',
    gstRate: 18,
    category: 'SMARTWATCH',
  },
  '910500': {
    code: '910500',
    description: 'Clocks (wall clocks, alarm clocks)',
    gstRate: 18,
    category: 'CLOCK',
  },
  // Chapter 90: Hearing aids (NIL / exempt for complete devices; parts 18%)
  '902140': {
    code: '902140',
    description: 'Hearing aids (complete device) — NIL/exempt; parts attract 18%',
    gstRate: 0,
    category: 'HEARING_AID',
  },
  // Accessories & Services
  '392690': {
    code: '392690',
    description: 'Spectacle cases, cleaning cloths, accessories (plastics)',
    gstRate: 18,
    category: 'ACCESSORIES',
  },
  '998599': {
    code: '998599',
    description: 'Optical services (fitting, repair, adjustment)',
    gstRate: 18,
    category: 'SERVICE',
  },
};


// ============================================================================
// Category → GST Rate — THE ONE LOCAL GST TABLE, and the only one left
// ============================================================================
// Its single caller is gstRuntime.resolveGstRate(), as the LAST resort, for the
// window before GET /products/gst-rates has answered. It stays hand-written on
// purpose: it sits on the POS billing path, so what it returns offline has to
// stay byte-for-byte what it returned yesterday. Everything else that used to
// be mirrored here (the category → HSN map, the category → master-row hint) is
// now read off the server — see constants/gstRuntime.ts.
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
    default:
      return 18; // Conservative default
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
export function getHSNOptions(): Array<{ value: string; label: string; gstRate: number }> {
  const codes = HSN_CODES;
  return Object.values(codes).map((hsn) => ({
    value: hsn.code,
    label: `${hsn.code} - ${hsn.description} (GST: ${hsn.gstRate}%)`,
    gstRate: hsn.gstRate,
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
  getHSNOptions,
  GSTR1_SECTIONS,
  GSTR3B_TABLES,
};

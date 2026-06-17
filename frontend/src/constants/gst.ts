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

// Simplified 4-digit HSN codes for businesses with turnover up to ₹5 Cr
export const HSN_CODES_4_DIGIT: Record<string, HSNCode> = {
  '9001': {
    code: '9001',
    description: 'Optical lenses, prisms, mirrors',
    gstRate: 5,    // GST 2.0
    category: 'LENS',
  },
  '9003': {
    code: '9003',
    description: 'Frames and mountings for spectacles',
    gstRate: 5,    // GST 2.0: reduced from 18%
    category: 'FRAME',
  },
  '9004': {
    code: '9004',
    description: 'Spectacles, goggles and the like',
    gstRate: 5,    // GST 2.0: corrective = 5%, sunglasses = 18%
    category: 'SPECTACLE',
  },
  '9101': {
    code: '9101',
    description: 'Wrist watches',
    gstRate: 18,
    category: 'WATCH',
  },
  '9102': {
    code: '9102',
    description: 'Electronic watches, smart watches',
    gstRate: 18,
    category: 'SMARTWATCH',
  },
  '9105': {
    code: '9105',
    description: 'Clocks (wall / alarm)',
    gstRate: 18,
    category: 'CLOCK',
  },
  '9021': {
    code: '9021',
    description: 'Hearing aids (complete device) — NIL/exempt; parts 18%',
    gstRate: 0,
    category: 'HEARING_AID',
  },
};

// ============================================================================
// Category → GST Rate mapping (used by POS for quick rate lookup)
// ============================================================================
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

// Get HSN code by product category
export function getHSNByCategory(category: string, use6Digit: boolean = false): HSNCode | null {
  // Accepts canonical enum codes, seed plural forms, and the short UI codes
  // (FR/LS/RG/CL/SG/WT/CK/HA/ACC/SMT*) used on AddProductPage.
  if (use6Digit) {
    switch (category.toUpperCase()) {
      case 'CONTACT_LENS':
      case 'CONTACT_LENSES':
      case 'COLORED_CONTACT_LENS':
      case 'COLOUR_CONTACTS':
      case 'CL':
        return HSN_CODES['900130'];
      case 'LENS':
      case 'RX_LENSES':
      case 'OPTICAL_LENS':
      case 'EYEGLASS_LENS':
      case 'LS':
        return HSN_CODES['900150'];
      case 'FRAME':
      case 'FRAMES':
      case 'EYEGLASS_FRAME':
      case 'FR':
        return HSN_CODES['900311'];
      case 'SPECTACLE':
      case 'COMPLETE_SPECTACLE':
      case 'READING_GLASSES':
      case 'RG':
        return HSN_CODES['900490'];
      case 'SUNGLASSES':
      case 'SUNGLASS':
      case 'SG':
        return HSN_CODES['900410'];
      case 'WRIST_WATCHES':
      case 'WATCH':
      case 'WT':
        return HSN_CODES['910111'];
      case 'SMARTWATCHES':
      case 'SMARTWATCH':
      case 'SMTWT':
        return HSN_CODES['910221'];
      case 'WALL_CLOCK':
      case 'WALL_CLOCKS':
      case 'CK':
        return HSN_CODES['910500'];
      case 'HEARING_AID':
      case 'HEARING_AIDS':
      case 'HA':
        return HSN_CODES['902140'];
      case 'SMARTGLASSES':
      case 'SMTSG':
      case 'SMTFR':
        return HSN_CODES['900410']; // GST-REVIEW: electronic eyewear, 18% placeholder
      case 'ACCESSORIES':
      case 'ACC':
        return HSN_CODES['392690'];
      case 'SERVICE':
      case 'SERVICES':
        return HSN_CODES['998599'];
      default:
        return HSN_CODES['900490']; // Default to corrective spectacles (5%)
    }
  } else {
    switch (category.toUpperCase()) {
      case 'CONTACT_LENS':
      case 'CONTACT_LENSES':
      case 'COLORED_CONTACT_LENS':
      case 'COLOUR_CONTACTS':
      case 'CL':
      case 'LENS':
      case 'RX_LENSES':
      case 'OPTICAL_LENS':
      case 'EYEGLASS_LENS':
      case 'LS':
        return HSN_CODES_4_DIGIT['9001'];
      case 'FRAME':
      case 'FRAMES':
      case 'EYEGLASS_FRAME':
      case 'FR':
        return HSN_CODES_4_DIGIT['9003'];
      case 'SPECTACLE':
      case 'COMPLETE_SPECTACLE':
      case 'READING_GLASSES':
      case 'RG':
      case 'SUNGLASSES':
      case 'SUNGLASS':
      case 'SG':
        return HSN_CODES_4_DIGIT['9004'];
      case 'WRIST_WATCHES':
      case 'WATCH':
      case 'WT':
        return HSN_CODES_4_DIGIT['9101'];
      case 'SMARTWATCHES':
      case 'SMARTWATCH':
      case 'SMTWT':
        return HSN_CODES_4_DIGIT['9102'];
      case 'WALL_CLOCK':
      case 'WALL_CLOCKS':
      case 'CK':
        return HSN_CODES_4_DIGIT['9105'];
      case 'HEARING_AID':
      case 'HEARING_AIDS':
      case 'HA':
        return HSN_CODES_4_DIGIT['9021'];
      default:
        return HSN_CODES_4_DIGIT['9004'];
    }
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

// Get all HSN codes as dropdown options
export function getHSNOptions(use6Digit: boolean = false): Array<{ value: string; label: string; gstRate: number }> {
  const codes = use6Digit ? HSN_CODES : HSN_CODES_4_DIGIT;
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
  HSN_CODES_4_DIGIT,
  getHSNByCategory,
  calculateGST,
  calculateIGST,
  validateGSTNumber,
  getHSNOptions,
  GSTR1_SECTIONS,
  GSTR3B_TABLES,
};

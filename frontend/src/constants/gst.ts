// ============================================================================
// IMS 2.0 - GST Constants and Utilities
// ============================================================================
// HSN codes, tax rates, and GST utilities for Indian optical retail

export interface HSNCode {
  code: string;
  description: string;
  gstRate: number;
  category: 'LENS' | 'FRAME' | 'SPECTACLE' | 'CONTACT_LENS' | 'SUNGLASSES' | 'ACCESSORIES';
}

// HSN Codes for Optical Products (as per Indian GST)
export const HSN_CODES: Record<string, HSNCode> = {
  // Chapter 90: Optical, photographic, cinematographic apparatus
  '900130': {
    code: '900130',
    description: 'Contact lenses',
    gstRate: 12,
    category: 'CONTACT_LENS',
  },
  '900140': {
    code: '900140',
    description: 'Spectacle lenses of glass',
    gstRate: 12,
    category: 'LENS',
  },
  '900150': {
    code: '900150',
    description: 'Spectacle lenses of other materials',
    gstRate: 12,
    category: 'LENS',
  },
  '900311': {
    code: '900311',
    description: 'Frames of plastics for spectacles',
    gstRate: 18,
    category: 'FRAME',
  },
  '900319': {
    code: '900319',
    description: 'Frames of other materials for spectacles',
    gstRate: 18,
    category: 'FRAME',
  },
  '900400': {
    code: '900400',
    description: 'Spectacles, goggles and the like, corrective, protective or other',
    gstRate: 12,
    category: 'SPECTACLE',
  },
  '900490': {
    code: '900490',
    description: 'Sunglasses',
    gstRate: 18,
    category: 'SUNGLASSES',
  },
};

// Simplified 4-digit HSN codes for businesses with turnover up to ₹5 Cr
export const HSN_CODES_4_DIGIT: Record<string, HSNCode> = {
  '9001': {
    code: '9001',
    description: 'Optical fibres and bundles; optical cables; lenses, prisms, mirrors',
    gstRate: 12,
    category: 'LENS',
  },
  '9003': {
    code: '9003',
    description: 'Frames and mountings for spectacles',
    gstRate: 18,
    category: 'FRAME',
  },
  '9004': {
    code: '9004',
    description: 'Spectacles, goggles and the like',
    gstRate: 12,
    category: 'SPECTACLE',
  },
};

// Get HSN code by product category
export function getHSNByCategory(category: string, use6Digit: boolean = false): HSNCode | null {
  if (use6Digit) {
    // Return 6-digit HSN codes
    switch (category.toUpperCase()) {
      case 'CONTACT_LENS':
        return HSN_CODES['900130'];
      case 'LENS':
      case 'EYEGLASS_LENS':
        return HSN_CODES['900140'];
      case 'FRAME':
      case 'EYEGLASS_FRAME':
        return HSN_CODES['900311'];
      case 'SPECTACLE':
      case 'COMPLETE_SPECTACLE':
        return HSN_CODES['900400'];
      case 'SUNGLASSES':
        return HSN_CODES['900490'];
      default:
        return HSN_CODES['900400']; // Default to spectacles
    }
  } else {
    // Return 4-digit HSN codes
    switch (category.toUpperCase()) {
      case 'CONTACT_LENS':
      case 'LENS':
      case 'EYEGLASS_LENS':
        return HSN_CODES_4_DIGIT['9001'];
      case 'FRAME':
      case 'EYEGLASS_FRAME':
        return HSN_CODES_4_DIGIT['9003'];
      case 'SPECTACLE':
      case 'COMPLETE_SPECTACLE':
      case 'SUNGLASSES':
        return HSN_CODES_4_DIGIT['9004'];
      default:
        return HSN_CODES_4_DIGIT['9004']; // Default to spectacles
    }
  }
}

// Calculate GST components
export function calculateGST(amount: number, gstRate: number) {
  const gstAmount = (amount * gstRate) / (100 + gstRate);
  const cgst = gstAmount / 2;
  const sgst = gstAmount / 2;
  const baseAmount = amount - gstAmount;

  return {
    baseAmount: parseFloat(baseAmount.toFixed(2)),
    cgst: parseFloat(cgst.toFixed(2)),
    sgst: parseFloat(sgst.toFixed(2)),
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

// ============================================================================
// IMS 2.0 - Validation Utilities
// ============================================================================
// Centralized validation logic to eliminate duplication across 8+ validation types

/**
 * Validates email format
 * Used in: AddCustomerModal, multiple forms
 */
export const validators = {
  email: (value: string): boolean => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(value);
  },

  /**
   * Validates phone number (10 digits for India)
   * Used in: AddCustomerModal, CustomerSearch, multiple forms
   */
  phone: (value: string): boolean => {
    const phoneRegex = /^\d{10}$/;
    return phoneRegex.test(value.replace(/\D/g, ''));
  },

  /**
   * Validates GST number (Indian GST format)
   * Used in: Business settings, invoice generation
   */
  gst: (value: string): boolean => {
    const gstRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
    return gstRegex.test(value.toUpperCase());
  },

  /**
   * Validates EAN-13 barcode
   * Used in: BarcodeManagementModal, barcode generation
   */
  ean13: (value: string): boolean => {
    const ean13Regex = /^\d{13}$/;
    return ean13Regex.test(value);
  },

  /**
   * Validates UPC barcode (12 digits)
   * Used in: BarcodeManagementModal
   */
  upc: (value: string): boolean => {
    const upcRegex = /^\d{12}$/;
    return upcRegex.test(value);
  },

  /**
   * Validates CODE128 barcode format
   * Used in: BarcodeManagementModal
   */
  code128: (value: string): boolean => {
    // CODE128 can contain ASCII 0-127, typically printable chars
    return /^[\x00-\x7F]{1,}$/.test(value);
  },

  /**
   * Validates that a value is a positive number
   * Used in: DiscountModal, ReorderPointModal, POS system
   */
  positiveNumber: (value: number): boolean => {
    return !isNaN(value) && value > 0;
  },

  /**
   * Validates percentage (0-100)
   * Used in: DiscountModal, margin calculations
   */
  percentage: (value: number): boolean => {
    return !isNaN(value) && value >= 0 && value <= 100;
  },

  /**
   * Validates URL format
   * Used in: Settings, image URLs
   */
  url: (value: string): boolean => {
    try {
      new URL(value);
      return true;
    } catch {
      return false;
    }
  },

  /**
   * Validates credit card number (Luhn algorithm)
   * Used in: Payment processing
   */
  creditCard: (value: string): boolean => {
    const sanitized = value.replace(/\D/g, '');
    if (sanitized.length < 13 || sanitized.length > 19) return false;

    // Luhn algorithm
    let sum = 0;
    let isEven = false;

    for (let i = sanitized.length - 1; i >= 0; i--) {
      let digit = parseInt(sanitized.charAt(i), 10);

      if (isEven) {
        digit *= 2;
        if (digit > 9) digit -= 9;
      }

      sum += digit;
      isEven = !isEven;
    }

    return sum % 10 === 0;
  },

  /**
   * Validates date format (YYYY-MM-DD)
   * Used in: Date inputs, filters
   */
  date: (value: string): boolean => {
    const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
    if (!dateRegex.test(value)) return false;

    const date = new Date(value);
    return date instanceof Date && !isNaN(date.getTime());
  },

  /**
   * Validates that a value is not empty/null/undefined
   * Used in: Form validation
   */
  required: (value: any): boolean => {
    if (value === null || value === undefined) return false;
    if (typeof value === 'string') return value.trim().length > 0;
    return true;
  },

  /**
   * Validates minimum length
   * Used in: Password validation, text fields
   */
  minLength: (value: string, min: number): boolean => {
    return value.length >= min;
  },

  /**
   * Validates maximum length
   * Used in: Text fields, descriptions
   */
  maxLength: (value: string, max: number): boolean => {
    return value.length <= max;
  },

  /**
   * Validates that value matches a pattern
   * Used in: Custom regex validation
   */
  pattern: (value: string, pattern: RegExp): boolean => {
    return pattern.test(value);
  },
};

/**
 * Number utilities - consolidates number handling from DiscountModal, ReorderPointModal, PaymentModal
 */
export const numberUtils = {
  /**
   * Clamp a number between min and max
   * Used in: DiscountModal (clamp 0-100%), ReorderPointModal (clamp > 0)
   */
  clamp: (value: number, min: number, max: number): number => {
    return Math.max(min, Math.min(max, value));
  },

  /**
   * Round to specified decimal places
   * Used in: Price calculations, currency formatting
   */
  round: (value: number, decimals: number = 2): number => {
    return Math.round(value * Math.pow(10, decimals)) / Math.pow(10, decimals);
  },

  /**
   * Format number with thousand separators
   * Used in: Stock counts, large numbers
   */
  formatNumber: (value: number): string => {
    return new Intl.NumberFormat('en-IN').format(value);
  },

  /**
   * Parse number from string, handling various formats
   * Used in: Number inputs with formatting
   */
  parseNumber: (value: string): number => {
    return parseFloat(value.replace(/[^\d.-]/g, ''));
  },
};

/**
 * Validation error messages - centralized for consistency
 */
export const validationMessages = {
  email: 'Please enter a valid email address',
  phone: 'Phone number must be 10 digits',
  gst: 'Please enter a valid GST number',
  ean13: 'EAN-13 must be 13 digits',
  upc: 'UPC must be 12 digits',
  positiveNumber: 'Please enter a positive number',
  percentage: 'Percentage must be between 0 and 100',
  url: 'Please enter a valid URL',
  creditCard: 'Please enter a valid credit card number',
  date: 'Please enter a valid date (YYYY-MM-DD)',
  required: 'This field is required',
  minLength: (min: number) => `Minimum length is ${min} characters`,
  maxLength: (max: number) => `Maximum length is ${max} characters`,
};

/**
 * Combined validator - validates multiple rules
 * Usage: combineValidators([
 *   { rule: validators.required, message: validationMessages.required },
 *   { rule: (v) => validators.email(v), message: validationMessages.email },
 * ])(value)
 */
export function combineValidators(
  rules: Array<{
    rule: (value: any) => boolean;
    message: string;
  }>
) {
  return (value: any): { isValid: boolean; error?: string } => {
    for (const { rule, message } of rules) {
      if (!rule(value)) {
        return { isValid: false, error: message };
      }
    }
    return { isValid: true };
  };
}

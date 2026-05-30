// ============================================================================
// IMS 2.0 - Shared field validators (Indian statutory + contact formats)
// ============================================================================
// Each validator returns `null` when the value is valid OR empty (callers
// enforce required-ness separately), else a short human-readable message
// suitable for a toast. Used across Settings modals (entities, stores, users)
// so master data (GSTIN / PAN / IFSC / pincode / phone / geo-fence) can't be
// saved malformed.

/** 15-char GSTIN: 2 state code + 10 PAN + 1 entity + 1 alnum + 'Z' + checksum. */
export function validateGstin(v?: string | null): string | null {
  if (!v) return null;
  const s = v.trim().toUpperCase();
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/.test(s)) {
    return 'GSTIN must be 15 characters (e.g. 22AAAAA0000A1Z5).';
  }
  return null;
}

/** 10-char PAN: 5 alpha + 4 digit + 1 alpha. */
export function validatePan(v?: string | null): string | null {
  if (!v) return null;
  if (!/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(v.trim().toUpperCase())) {
    return 'PAN must be 10 characters (e.g. AAAAA0000A).';
  }
  return null;
}

/** 11-char IFSC: 4 bank alpha + '0' + 6 branch alnum. */
export function validateIfsc(v?: string | null): string | null {
  if (!v) return null;
  if (!/^[A-Z]{4}0[A-Z0-9]{6}$/.test(v.trim().toUpperCase())) {
    return 'IFSC must be 11 characters (e.g. HDFC0001234).';
  }
  return null;
}

/** 6-digit Indian pincode (not starting with 0). */
export function validatePincode(v?: string | null): string | null {
  if (!v) return null;
  if (!/^[1-9][0-9]{5}$/.test(String(v).trim())) {
    return 'Pincode must be 6 digits.';
  }
  return null;
}

/** 10-digit Indian mobile (tolerates spaces / +91 prefix). */
export function validatePhone(v?: string | null): string | null {
  if (!v) return null;
  const digits = String(v).replace(/\D/g, '').replace(/^91(?=\d{10}$)/, '');
  if (!/^[6-9][0-9]{9}$/.test(digits)) {
    return 'Phone must be a 10-digit Indian mobile number.';
  }
  return null;
}

/** Geo-fence radius in metres — store-staff login fence (SYSTEM_INTENT: 500m default). */
export function validateGeoRadius(v?: number | string | null): string | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n < 100 || n > 2000) {
    return 'Geo-fence radius must be between 100 and 2000 metres.';
  }
  return null;
}

/** Returns the first non-null error among the given validator results (or null). */
export function firstError(...errs: (string | null)[]): string | null {
  return errs.find((e) => e) ?? null;
}

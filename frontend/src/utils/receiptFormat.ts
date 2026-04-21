// ============================================================================
// IMS 2.0 — Customer-facing invoice formatting helpers
// ============================================================================
// Per Avinash's Phase 6.7 request: customer receipts should read
// "Ray-Ban Sunglass · Zeiss Spectacle Lens · Total ₹8,900" — brand +
// category label, not the full SKU + model string we show internally.
//
// Legal GST invoice (GSTInvoice.tsx) still carries the full product
// description + HSN + per-line GST math as required by Indian GST law —
// these helpers do NOT touch that path.

/**
 * Product category → customer-friendly label.
 * Covers the canonical categories used across the product catalog.
 */
export function categoryLabel(category: string | null | undefined): string {
  if (!category) return 'Item';
  const c = String(category).toUpperCase().replace(/[-_\s]+/g, '_');
  switch (c) {
    case 'FRAMES':
    case 'FRAME':
    case 'SPECTACLE_FRAME':
    case 'SPECTACLE_FRAMES':
      return 'Spectacle Frame';

    case 'SUNGLASSES':
    case 'SUNGLASS':
      return 'Sunglass';

    case 'OPTICAL_LENS':
    case 'OPTICAL_LENSES':
    case 'SPECTACLE_LENS':
    case 'SPECTACLE_LENSES':
    case 'LENS':
    case 'LENSES':
      return 'Spectacle Lens';

    case 'CONTACT_LENS':
    case 'CONTACT_LENSES':
    case 'CONTACTLENS':
      return 'Contact Lens';

    case 'READING_GLASSES':
    case 'READERS':
      return 'Reading Glasses';

    case 'WATCH':
    case 'WATCHES':
    case 'MECHANICAL_WATCH':
    case 'QUARTZ_WATCH':
    case 'SMART_WATCH':
      return 'Watch';

    case 'ACCESSORIES':
    case 'ACCESSORY':
    case 'CASE':
    case 'CLOTH':
      return 'Accessory';

    case 'SERVICE':
    case 'SERVICES':
    case 'REPAIR':
      return 'Service';

    default:
      // Fallback: Title-case the raw category so we never show UPPER_CASE
      // in front of a customer.
      return c
        .split('_')
        .map(w => w.charAt(0) + w.slice(1).toLowerCase())
        .join(' ');
  }
}

/**
 * Customer-facing line description.
 *   Input  : { brand: 'Ray-Ban', category: 'SUNGLASSES', name: 'Wayfarer RB2140' }
 *   Output : 'Ray-Ban Sunglass'
 *
 * Falls back to the product name when the brand is missing (generics,
 * custom Rx lenses, services) — we still surface *something* readable.
 */
export function describeForReceipt(item: {
  brand?: string | null;
  subbrand?: string | null;
  category?: string | null;
  name?: string | null;
}): string {
  const brand = (item.brand || '').trim();
  const cat = categoryLabel(item.category);
  if (brand) {
    return `${brand} ${cat}`;
  }
  // No brand — prefer subbrand, else product name, else just the category
  const sub = (item.subbrand || '').trim();
  if (sub) return `${sub} ${cat}`;
  const n = (item.name || '').trim();
  return n || cat;
}

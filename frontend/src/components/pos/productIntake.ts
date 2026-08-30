// ============================================================================
// IMS 2.0 - POS product intake (scan resolution + price-integrity guard)
// ============================================================================
// Extracted from POSLayout (Wave 4) so the classic POS and the new
// one-surface POS share ONE copy of the scan path and — critically — the
// MONEY guards (offer>MRP block, zero/NaN price block). Never re-inline.

import { inventoryApi } from '../../services/api';
import { canonicalCategory } from '../../utils/categoryNormalize';

export interface ScanResolution {
  ok: boolean;
  product?: any;
  message?: string;
}

/** Resolve an intake barcode to a cart-ready product, store-scoped.
    Fail-loudly contract: any miss returns a message; NOTHING is guessed. */
export async function resolveBarcode(
  storeId: string,
  barcode: string,
): Promise<ScanResolution> {
  const code = (barcode || '').trim();
  if (!code) return { ok: false };
  try {
    const hit = await inventoryApi.searchByBarcode(code, storeId || '');
    // A foreign-store unit must NOT be quietly sold at this terminal.
    if (hit?.cross_store) {
      return {
        ok: false,
        message: `Barcode ${code} belongs to another store's stock -- it cannot be sold here.`,
      };
    }
    // Build a cart-ready product from the joined product master (the scan
    // endpoint joins `products` onto the `stock_units` row); fall back to the
    // unit's own fields if the join is absent.
    const p = hit?.product || {};
    const product = {
      product_id: p.product_id || hit?.product_id,
      name: p.name || p.model || hit?.product_name,
      sku: p.sku || hit?.sku,
      barcode: hit?.barcode || code,
      brand: p.brand,
      subbrand: p.subbrand || p.sub_brand,
      category: p.category || hit?.category,
      hsn_code: p.hsn_code || hit?.hsn_code,
      mrp: p.mrp,
      offer_price: p.offer_price ?? p.offerPrice,
      image_url: p.image_url,
    };
    if (product.product_id) return { ok: true, product };
    // Hit with no resolvable product -- loud-fail rather than swallow it.
    return {
      ok: false,
      message: `Barcode ${code} found but its product record is missing. Tell the manager.`,
    };
  } catch {
    // 404 (or any error) = this barcode is not in stock. Fail loudly; add
    // NOTHING (do not dump the scanned value into the search box).
    return {
      ok: false,
      message: `Barcode ${code} not found in stock. Check the item or search by name.`,
    };
  }
}

export interface PriceGuardResult {
  ok: boolean;
  message?: string;
  finalPrice?: number;
  mrp?: number;
  offerPrice?: number;
}

const fc = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;

/** The POS price-integrity gate (MONEY): offer>MRP and zero/NaN pricing are
    hard blocks at add time — the backend enforces the same on create. */
export function posPriceGuard(product: any): PriceGuardResult {
  const mrp = product.mrp || 0;
  const offerPrice = product.offer_price || product.offerPrice || mrp;
  if (offerPrice > mrp && mrp > 0) {
    return {
      ok: false,
      message: `BLOCKED: ${product.name} -- Offer Price (${fc(offerPrice)}) exceeds MRP (${fc(mrp)}). Contact HQ to fix pricing.`,
    };
  }
  const finalPrice = offerPrice || mrp;
  if (!finalPrice || finalPrice <= 0 || isNaN(finalPrice)) {
    return {
      ok: false,
      message: `BLOCKED: ${product.name} -- Invalid pricing (${fc(finalPrice)}). Contact HQ to fix.`,
    };
  }
  return { ok: true, finalPrice, mrp, offerPrice };
}

/** Cart-line shape from a guarded product — the exact mapping the classic
    surface uses (hsn_code carried so the tax invoice prints the registered
    code; is_optical drives the Rx surfaces). */
export function cartItemFromProduct(product: any, guard: PriceGuardResult) {
  const { finalPrice, mrp, offerPrice } = guard;
  return {
    product_id: product.product_id || product._id || product.id,
    name: product.name,
    sku: product.sku,
    barcode: product.barcode,
    brand: product.brand,
    subbrand: product.subbrand || product.sub_brand,
    category: product.category,
    hsn_code: product.hsn_code,
    unit_price: finalPrice!,
    mrp: mrp || 0,
    offer_price: offerPrice !== mrp ? offerPrice : undefined,
    quantity: 1,
    is_optical: ['FRAME', 'OPTICAL_LENS', 'CONTACT_LENS', 'COLORED_CONTACT_LENS'].includes(
      canonicalCategory(product.category),
    ),
    image_url: product.image_url,
  };
}

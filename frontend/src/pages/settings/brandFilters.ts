// ============================================================================
// IMS 2.0 - Brand Master filters (Settings > Brands)
// ============================================================================
// Pure logic: read/write the filter from the page address (so a refresh keeps
// it) and filter + sort the brand list. All filters combine (AND). The list
// is loaded whole from GET /admin/brands (not paginated), so this is client-
// side.

import type { Brand } from './settingsTypes';
import { canonicalCategory } from '../../utils/categoryNormalize';

export type BrandSort = 'az' | 'products' | 'recent';

export interface BrandFilters {
  /** Brand name, brand code or sub-brand name (contains, any case). */
  q: string;
  /** Canonical category (FRAME, SUNGLASS, ...); '' = any. */
  cat: string;
  /** Shown on the website (sync to Shopify default). */
  web: '' | 'on' | 'off';
  tier: '' | Brand['tier'];
  sub: '' | 'yes' | 'no';
  status: '' | 'active' | 'inactive';
  /** Never sell online: 'blocked' = set, 'allowed' = not set. */
  online: '' | 'allowed' | 'blocked';
  sort: BrandSort;
}

export const EMPTY_BRAND_FILTERS: BrandFilters = {
  q: '', cat: '', web: '', tier: '', sub: '', status: '', online: '', sort: 'az',
};

const KEYS = Object.keys(EMPTY_BRAND_FILTERS) as (keyof BrandFilters)[];

const pick = <T extends string>(v: string | null, allowed: readonly T[], fallback: T): T =>
  allowed.includes(v as T) ? (v as T) : fallback;

export function readBrandFilters(sp: URLSearchParams): BrandFilters {
  return {
    q: sp.get('q') || '',
    cat: canonicalCategory(sp.get('cat')),
    web: pick(sp.get('web'), ['', 'on', 'off'] as const, ''),
    tier: pick(sp.get('tier'), ['', 'MASS', 'PREMIUM', 'LUXURY'] as const, ''),
    sub: pick(sp.get('sub'), ['', 'yes', 'no'] as const, ''),
    status: pick(sp.get('status'), ['', 'active', 'inactive'] as const, ''),
    online: pick(sp.get('online'), ['', 'allowed', 'blocked'] as const, ''),
    sort: pick(sp.get('sort'), ['az', 'products', 'recent'] as const, 'az'),
  };
}

/** Writes only non-default values; other params in the address are kept. */
export function writeBrandFilters(f: BrandFilters, prev: URLSearchParams): URLSearchParams {
  const sp = new URLSearchParams(prev);
  for (const k of KEYS) {
    const v = k === 'q' ? f.q.trim() : f[k];
    if (v && v !== EMPTY_BRAND_FILTERS[k]) sp.set(k, v);
    else sp.delete(k);
  }
  return sp;
}

/** True when any filter (not the sort) narrows the list. */
export function isBrandFiltered(f: BrandFilters): boolean {
  return KEYS.some((k) => k !== 'sort' && (k === 'q' ? f.q.trim() !== '' : f[k] !== ''));
}

/** Clears the filters; the chosen sort stays. */
export function clearBrandFilters(f: BrandFilters): BrandFilters {
  return { ...EMPTY_BRAND_FILTERS, sort: f.sort };
}

/** Canonical categories that at least one brand uses, for the select. */
export function brandCategoryOptions(brands: Brand[]): string[] {
  return [...new Set(brands.flatMap((b) => b.categories.map(canonicalCategory)).filter(Boolean))].sort();
}

export function filterBrands(brands: Brand[], f: BrandFilters): Brand[] {
  const q = f.q.trim().toLowerCase();
  const out = brands.filter((b) => {
    if (q) {
      const hay = [b.brandName, b.brandCode, ...b.subbrands.map((s) => s.name)];
      if (!hay.some((h) => (h || '').toLowerCase().includes(q))) return false;
    }
    if (f.cat && !b.categories.some((c) => canonicalCategory(c) === f.cat)) return false;
    if (f.web && b.syncToShopifyDefault !== (f.web === 'on')) return false;
    if (f.tier && b.tier !== f.tier) return false;
    if (f.sub && (b.subbrands.length > 0) !== (f.sub === 'yes')) return false;
    if (f.status && b.isActive !== (f.status === 'active')) return false;
    if (f.online && b.neverSellOnline !== (f.online === 'blocked')) return false;
    return true;
  });
  const byName = (a: Brand, b: Brand) => a.brandName.localeCompare(b.brandName, 'en', { sensitivity: 'base' });
  if (f.sort === 'products') {
    // Brands whose count is unknown go last.
    out.sort((a, b) => (b.productCount ?? -1) - (a.productCount ?? -1) || byName(a, b));
  } else if (f.sort === 'recent') {
    // ISO timestamps sort as text; brands with none go last.
    out.sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || '') || byName(a, b));
  } else {
    out.sort(byName);
  }
  return out;
}

/** The create/edit body for a brand form. never_sell_online is sent only
 *  when the form changed it, so a stale form never reverts a SUPERADMIN's
 *  block (the server refuses a change by anyone else). */
export function brandSavePayload(form: Partial<Brand>, original: Brand | null) {
  const block = form.neverSellOnline === true;
  return {
    name: form.brandName || '',
    code: form.brandCode || '',
    categories: form.categories || [],
    tier: form.tier || 'MASS',
    status: form.isActive ? 'ACTIVE' : 'INACTIVE',
    sync_to_shopify_default: form.syncToShopifyDefault === true,
    ...(block !== (original?.neverSellOnline === true) ? { never_sell_online: block } : {}),
  };
}

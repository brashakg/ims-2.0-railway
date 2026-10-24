import { describe, expect, it } from 'vitest';
import type { Brand } from '../settingsTypes';
import {
  EMPTY_BRAND_FILTERS,
  brandCategoryOptions,
  brandSavePayload,
  clearBrandFilters,
  filterBrands,
  isBrandFiltered,
  readBrandFilters,
  takeDownMessages,
  writeBrandFilters,
  type BrandFilters,
} from '../brandFilters';

const brand = (over: Partial<Brand>): Brand => ({
  id: over.brandName || 'x',
  brandName: 'X',
  brandCode: 'X',
  categories: [],
  tier: 'MASS',
  isActive: true,
  subbrands: [],
  syncToShopifyDefault: false,
  neverSellOnline: false,
  ...over,
});
const sb = (name: string) => ({ id: name, name, code: name, brandId: '', isActive: true });

const BRANDS: Brand[] = [
  brand({ brandName: 'Ray-Ban', brandCode: 'RB', categories: ['SG', 'FR'], tier: 'PREMIUM', syncToShopifyDefault: true, subbrands: [sb('Meta')], productCount: 40, updatedAt: '2026-10-01T10:00:00' }),
  brand({ brandName: 'acuvue', brandCode: 'ACU', categories: ['CONTACT_LENSES'], productCount: 5, updatedAt: '2026-10-18T09:00:00' }),
  brand({ brandName: 'Cartier', brandCode: 'CAR', categories: ['FRAME'], tier: 'LUXURY', isActive: false, neverSellOnline: true }),
];
const names = (f: Partial<BrandFilters>) =>
  filterBrands(BRANDS, { ...EMPTY_BRAND_FILTERS, ...f }).map((b) => b.brandName);

describe('filterBrands', () => {
  it('no filters: all brands, A-Z ignoring case', () => {
    expect(names({})).toEqual(['acuvue', 'Cartier', 'Ray-Ban']);
  });
  it('search matches brand name, code and sub-brand name', () => {
    expect(names({ q: 'ray' })).toEqual(['Ray-Ban']);
    expect(names({ q: 'acu' })).toEqual(['acuvue']);
    expect(names({ q: 'META' })).toEqual(['Ray-Ban']);
    expect(names({ q: '  ' })).toHaveLength(3);
  });
  it('category matches any spelling (SG / SUNGLASS, CONTACT_LENSES / CONTACT_LENS)', () => {
    expect(names({ cat: 'SUNGLASS' })).toEqual(['Ray-Ban']);
    expect(names({ cat: 'FRAME' })).toEqual(['Cartier', 'Ray-Ban']);
    expect(names({ cat: 'CONTACT_LENS' })).toEqual(['acuvue']);
  });
  it('website on / off', () => {
    expect(names({ web: 'on' })).toEqual(['Ray-Ban']);
    expect(names({ web: 'off' })).toEqual(['acuvue', 'Cartier']);
  });
  it('tier', () => {
    expect(names({ tier: 'LUXURY' })).toEqual(['Cartier']);
    expect(names({ tier: 'MASS' })).toEqual(['acuvue']);
  });
  it('has sub-brands yes / no', () => {
    expect(names({ sub: 'yes' })).toEqual(['Ray-Ban']);
    expect(names({ sub: 'no' })).toEqual(['acuvue', 'Cartier']);
  });
  it('active / inactive', () => {
    expect(names({ status: 'inactive' })).toEqual(['Cartier']);
    expect(names({ status: 'active' })).toEqual(['acuvue', 'Ray-Ban']);
  });
  it('online allowed / never sell online', () => {
    expect(names({ online: 'blocked' })).toEqual(['Cartier']);
    expect(names({ online: 'allowed' })).toEqual(['acuvue', 'Ray-Ban']);
  });
  it('filters combine (AND)', () => {
    expect(names({ cat: 'FRAME', status: 'active' })).toEqual(['Ray-Ban']);
    expect(names({ cat: 'FRAME', web: 'on', tier: 'LUXURY' })).toEqual([]);
  });
  it('sort by most products and recently edited (unknown last)', () => {
    expect(names({ sort: 'products' })).toEqual(['Ray-Ban', 'acuvue', 'Cartier']);
    expect(names({ sort: 'recent' })).toEqual(['acuvue', 'Ray-Ban', 'Cartier']);
  });
});

describe('address round-trip and clear', () => {
  it('writes only set values, keeps other params, reads back the same', () => {
    const f: BrandFilters = { q: 'ray ', cat: 'SUNGLASS', web: 'on', tier: 'PREMIUM', sub: 'yes', status: 'active', online: 'allowed', sort: 'products' };
    const sp = writeBrandFilters(f, new URLSearchParams('other=1'));
    expect(sp.get('other')).toBe('1');
    expect(sp.get('q')).toBe('ray');
    expect(readBrandFilters(sp)).toEqual({ ...f, q: 'ray' });
    expect(writeBrandFilters(EMPTY_BRAND_FILTERS, sp).toString()).toBe('other=1');
  });
  it('junk values in the address fall back to defaults', () => {
    expect(readBrandFilters(new URLSearchParams('web=maybe&tier=GOLD&sort=zzz&cat=sg'))).toEqual({
      ...EMPTY_BRAND_FILTERS,
      cat: 'SUNGLASS',
    });
  });
  it('clear removes every filter but keeps the sort', () => {
    const f: BrandFilters = { q: 'x', cat: 'FRAME', web: 'off', tier: 'MASS', sub: 'no', status: 'inactive', online: 'blocked', sort: 'recent' };
    expect(isBrandFiltered(f)).toBe(true);
    const c = clearBrandFilters(f);
    expect(isBrandFiltered(c)).toBe(false);
    expect(c.sort).toBe('recent');
    expect(isBrandFiltered({ ...EMPTY_BRAND_FILTERS, sort: 'products' })).toBe(false);
  });
  it('category options are the canonical categories brands use', () => {
    expect(brandCategoryOptions(BRANDS)).toEqual(['CONTACT_LENS', 'FRAME', 'SUNGLASS']);
  });
});

describe('brandSavePayload', () => {
  it('never submits a blocked brand with website sync enabled', () => {
    expect(brandSavePayload({ neverSellOnline: true, syncToShopifyDefault: true }, null).sync_to_shopify_default).toBe(false);
  });
  const blocked = brand({ brandName: 'Cartier', neverSellOnline: true });
  it('omits never_sell_online when the form did not change it', () => {
    expect(brandSavePayload({ ...blocked }, blocked)).not.toHaveProperty('never_sell_online');
    const open = brand({ brandName: 'Vogue' });
    expect(brandSavePayload({ ...open, tier: 'PREMIUM' }, open)).not.toHaveProperty('never_sell_online');
    expect(brandSavePayload({ brandName: 'New' }, null)).not.toHaveProperty('never_sell_online');
  });
  it('sends it when the form changed it, either way', () => {
    expect(brandSavePayload({ ...blocked, neverSellOnline: false }, blocked).never_sell_online).toBe(false);
    const open = brand({ brandName: 'Vogue' });
    expect(brandSavePayload({ ...open, neverSellOnline: true }, open).never_sell_online).toBe(true);
    expect(brandSavePayload({ brandName: 'New', neverSellOnline: true }, null).never_sell_online).toBe(true);
  });
  it('a SUPERADMIN save of a blocked brand re-sends true (re-runs the take-down)', () => {
    expect(brandSavePayload({ ...blocked }, blocked, true).never_sell_online).toBe(true);
    expect(brandSavePayload({ ...blocked }, blocked, false)).not.toHaveProperty('never_sell_online');
    const open = brand({ brandName: 'Vogue' });
    expect(brandSavePayload({ ...open }, open, true)).not.toHaveProperty('never_sell_online');
  });
});

describe('takeDownMessages (Never sell online ON takes live listings down)', () => {
  it('says the listings ARE being taken down when Shopify took them (LIVE)', () => {
    const m = takeDownMessages([
      { sku: 'A', ok: true, mode: 'LIVE' },
      { sku: 'B', ok: true, mode: 'LIVE' },
    ]);
    expect(m.warning).toMatch(/2 live listings are being taken down/);
    expect(m.error).toBeUndefined();
  });

  it('never claims a take-down while the push gates are dark (SIMULATED)', () => {
    const m = takeDownMessages([{ sku: 'A', ok: true, mode: 'SIMULATED' }]);
    expect(m.warning).toMatch(/would be taken down/);
    expect(m.warning).toMatch(/nothing changed on the website yet/);
  });

  it('names the listings whose take-down failed as still live', () => {
    const m = takeDownMessages([
      { sku: 'A', ok: true, mode: 'LIVE' },
      { sku: 'B', ok: false, mode: 'LIVE' },
    ]);
    expect(m.warning).toMatch(/^1 live listing is being taken down/);
    expect(m.error).toMatch(/1 listing could not be taken down and is still live: B/);
  });

  it('says nothing when the brand had no live listings', () => {
    expect(takeDownMessages([])).toEqual({});
  });
});

// ============================================================================
// The GST tables are server-fed -- and the POS fallback did not move
// ============================================================================
// constants/gst.ts used to hold a hand-mirrored copy of the backend's
// category -> HSN table, and gstRuntime.ts a hand-mirrored copy of its
// category -> master-row hint. Both are deleted; both now arrive on
// GET /products/gst-rates. The one table that survives is
// getGSTRateByCategory(), the OFFLINE rate fallback, because it sits on the POS
// billing path and must keep answering exactly what it answered yesterday.
//
// FIXTURES ARE CHOSEN SO THE TWO SIDES DISAGREE. Smartglasses were the drift:
// the deleted frontend copy said HSN 900410 (sunglasses) where the server says
// 852580 -- while BOTH said 18%. A fixture whose fields carry the same value
// cannot tell which one the code read, so every assertion below turns on a
// value the old copies would have got wrong.

import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
vi.mock('../../services/api/client', () => ({ default: { get: (...a: any[]) => mockGet(...a) } }));

// PRODUCTION SHAPE: exactly what the widened endpoint sends today -- the
// hsn_gst_master as seeded, plus the two static maps. Note 852580 is NOT in
// by_hsn (the master has no smartglasses row), so a smartglasses rate has to
// come down the category path; that is the real production situation.
const LIVE = {
  by_hsn: { '392690': 18, '900130': 5, '900150': 5, '900311': 5, '900410': 18,
            '900490': 5, '902140': 0, '910111': 18, '910221': 18, '998599': 18 },
  by_cat: { ACCESSORIES: 18, CONTACT_LENS: 5, FRAME: 5, HEARING_AID: 0, LENS: 5,
            SERVICE: 18, SMARTWATCH: 18, SPECTACLE: 5, SUNGLASSES: 18, WATCH: 18 },
  category_hint: {
    ACCESSORIES: 'ACCESSORIES', CCL: 'COLORED_CONTACT_LENS',
    COLORED_CONTACT_LENS: 'COLORED_CONTACT_LENS',
    COLORED_CONTACT_LENSES: 'COLORED_CONTACT_LENS', CONTACT_LENS: 'CONTACT_LENS',
    FRAME: 'FRAME', HEARING_AID: 'HEARING_AID', OPTICAL_LENS: 'LENS',
    READING_GLASSES: 'SPECTACLE', SERVICES: 'SERVICE', SMARTWATCH: 'SMARTWATCH',
    SUNGLASS: 'SUNGLASSES', WATCH: 'WATCH',
  },
  hsn_by_category: {
    ACCESSORIES: '392690', CCL: '900130', COLORED_CONTACT_LENS: '900130',
    CONTACT_LENS: '900130', FRAME: '900311', HEARING_AID: '902140',
    OPTICAL_LENS: '900150', READING_GLASSES: '900490', SERVICES: '998599',
    SMARTGLASSES: '852580', SMTFR: '852580', SMTSG: '852580',
    SMARTWATCH: '910221', SUNGLASS: '900410', WALL_CLOCK: '910500', WATCH: '910111',
  },
};

// Same maps, but CONTACT_LENS carries a sentinel no real GST rate ever takes.
// The DELETED frontend hint map sent COLORED_CONTACT_LENSES to CONTACT_LENS;
// the server's hint map sends it to COLORED_CONTACT_LENS. If that copy ever
// comes back, the hint test below reads 12345 and dies.
const SENTINEL = { ...LIVE, by_cat: { ...LIVE.by_cat, CONTACT_LENS: 12345 } };

async function load(withServerData: boolean, payload: object = LIVE) {
  vi.resetModules();
  mockGet.mockReset();
  const runtime = await import('../gstRuntime');
  const shared = await import('../../pages/catalog/productAddShared');
  if (withServerData) {
    mockGet.mockResolvedValue({ data: payload });
    await runtime.loadHsnRates();
  }
  return { runtime, shared };
}

describe('category -> HSN comes from the server, not a copy on the frontend', () => {
  beforeEach(() => vi.resetModules());

  it('gives smartglasses 852580, the code the server holds -- not 900410', async () => {
    const { runtime } = await load(true);
    expect(runtime.resolveHsn('SMARTGLASSES')).toBe('852580');
    expect(runtime.resolveHsn('SMTSG')).toBe('852580');
    // Genuine sunglasses still get 900410, so the two are distinguishable.
    expect(runtime.resolveHsn('SUNGLASS')).toBe('900410');
  });

  it('prefills the cataloguing door from the server, HSN first then its rate', async () => {
    const { shared } = await load(true);
    // The deleted copy had no CCL case: it returned 900490 (corrective
    // spectacles) at 18%. Colour contact lenses are 900130 at 5%.
    expect(shared.resolveHsnGst('CCL')).toEqual({ hsnCode: '900130', gstRate: '5' });
    // Smartglasses: right HSN, and the rate still 18% off the category path.
    expect(shared.resolveHsnGst('SMTSG')).toEqual({ hsnCode: '852580', gstRate: '18' });
  });

  it('sends no HSN at all before the endpoint answers, so the server fills its own', async () => {
    const { shared, runtime } = await load(false);
    expect(runtime.resolveHsn('SMTSG')).toBe('');
    expect(shared.resolveHsnGst('SMTSG').hsnCode).toBe('');
  });

  it('has no getHSNByCategory left to drift', async () => {
    const gst: Record<string, unknown> = await import('../gst');
    expect('getHSNByCategory' in gst).toBe(false);
    expect('getHSNByCategory' in ((gst.default ?? {}) as object)).toBe(false);
  });
});

describe('category -> master row comes from the server hint map', () => {
  it('routes COLORED_CONTACT_LENSES where the SERVER says, not where the old copy said', async () => {
    const { runtime } = await load(true, SENTINEL);
    // Server hint: -> COLORED_CONTACT_LENS, which the master has no row for, so
    // the offline table answers 5. Deleted copy: -> CONTACT_LENS -> 12345.
    expect(runtime.resolveGstRate('COLORED_CONTACT_LENSES')).toBe(5);
    // A plain contact lens still reads the master row, so the sentinel is live
    // and this fixture really can tell the two routes apart.
    expect(runtime.resolveGstRate('CONTACT_LENS')).toBe(12345);
  });
});

describe('THE POS CONSTRAINT: the offline rate fallback did not move', () => {
  // Every category a product can be stored with (product_master
  // all_category_specs) plus the blank one legacy rows carry. These are the
  // numbers main returned on 2026-08-27 before this change, in BOTH states.
  const FROZEN: Array<[string, number]> = [
    ['ACCESSORIES', 18],
    ['COLORED_CONTACT_LENS', 5],
    ['CONTACT_LENS', 5],
    ['FRAME', 5],
    ['HEARING_AID', 0],
    ['OPTICAL_LENS', 5],
    ['READING_GLASSES', 5],
    ['SERVICES', 18],
    ['SMARTGLASSES', 18],
    ['SMARTWATCH', 18],
    ['SUNGLASS', 18],
    ['WALL_CLOCK', 18],
    ['WATCH', 18],
    ['', 18],
  ];

  it('answers the same rate for every sellable category with NO server data', async () => {
    const { runtime } = await load(false);
    for (const [category, rate] of FROZEN) {
      expect([category, runtime.resolveGstRate(category)]).toEqual([category, rate]);
    }
  });

  it('answers the same rate for every sellable category WITH server data', async () => {
    const { runtime } = await load(true);
    for (const [category, rate] of FROZEN) {
      expect([category, runtime.resolveGstRate(category)]).toEqual([category, rate]);
    }
  });
});

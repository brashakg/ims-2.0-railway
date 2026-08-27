// ============================================================================
// The GST tables are server-fed -- and the offline fallback now AGREES with them
// ============================================================================
// constants/gst.ts used to hold a hand-mirrored copy of the backend's
// category -> HSN table, and gstRuntime.ts a hand-mirrored copy of its
// category -> master-row hint. Both are deleted; both now arrive on
// GET /products/gst-rates, together with the canonical category -> rate table.
//
// What survives on the frontend is getGSTRateByCategory(), the OFFLINE rate
// fallback for a browser that has never loaded the app. It is on the POS
// billing path, so the standard it is held to here is not "it did not change"
// -- it is "for every category the server names, it answers what the server
// bills". Measured on 2026-08-27, main failed that on 19 of 62 spellings cold
// and 9 of 62 loaded.
//
// FIXTURES ARE CHOSEN SO THE TWO SIDES DISAGREE. Smartglasses were the drift:
// the deleted frontend copy said HSN 900410 (sunglasses) where the server says
// 852580 -- while BOTH said 18%. A fixture whose fields carry the same value
// cannot tell which one the code read, so every assertion below turns on a
// value the old copies would have got wrong.

import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
vi.mock('../../services/api/client', () => ({ default: { get: (...a: any[]) => mockGet(...a) } }));

// A REAL localStorage. The runner hands tests an inert `localStorage` object
// with no methods on it (vitest starts node with --localstorage-file and no
// path, and that global shadows jsdom's), so every setItem/getItem in
// gstRuntime throws into its own catch and the snapshot path is invisible --
// which is why the pre-deploy window below was never exercised.
const STORE = (() => {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => m.clear(),
    key: (i: number) => [...m.keys()][i] ?? null,
    get length() { return m.size; },
  };
})();
vi.stubGlobal('localStorage', STORE);

// PRODUCTION SHAPE, in full: every key GET /products/gst-rates sends, with the
// hsn_gst_master as seeded (services/gst_rates.HSN_GST_SEED) and the three
// static maps straight out of GST_CATEGORY_TABLE / _CATEGORY_HINT. All 55
// categories, not a hand-picked subset -- an earlier version of this file
// listed 13 canonical spellings and so said nothing about CCL, ACCESSORY or
// SVC, three of the spellings that were actually wrong. Note 852580 and 9993
// are NOT in by_hsn (the master has no row for either), so their rates have to
// come down the category path; that is the real production situation.
const SERVER = {
  by_hsn: {
    '392690': 18, '900130': 5, '900150': 5, '900311': 5, '900410': 18,
    '900490': 5, '902140': 0, '910111': 18, '910221': 18, '998599': 18,
  },
  by_cat: {
    ACCESSORIES: 18, CONTACT_LENS: 5, FRAME: 5, HEARING_AID: 0, LENS: 5,
    SERVICE: 18, SMARTWATCH: 18, SPECTACLE: 5, SUNGLASSES: 18, WATCH: 18,
  },
  category_hint: {
    ACC: 'ACCESSORIES', ACCESSORIES: 'ACCESSORIES', ACCESSORY: 'ACCESSORIES',
    CCL: 'COLORED_CONTACT_LENS', CL: 'CONTACT_LENS',
    COLORED_CONTACT_LENS: 'COLORED_CONTACT_LENS',
    COLORED_CONTACT_LENSES: 'COLORED_CONTACT_LENS',
    COLOUR_CONTACTS: 'COLORED_CONTACT_LENS',
    COLOUR_CONTACT_LENS: 'COLORED_CONTACT_LENS',
    COMPLETE_SPECTACLE: 'SPECTACLE', CONTACT_LENS: 'CONTACT_LENS',
    CONTACT_LENSES: 'CONTACT_LENS', EYEGLASS_FRAME: 'FRAME',
    EYEGLASS_LENS: 'LENS', FR: 'FRAME', FRAME: 'FRAME', FRAMES: 'FRAME',
    HA: 'HEARING_AID', HEARING_AID: 'HEARING_AID',
    HEARING_AIDS: 'HEARING_AID', LENS: 'LENS', LENSES: 'LENS', LS: 'LENS',
    OPTICAL_LENS: 'LENS', OPTICAL_LENSES: 'LENS',
    READING_GLASSES: 'SPECTACLE', RG: 'SPECTACLE', RX_LENSES: 'LENS',
    SERVICE: 'SERVICE', SERVICES: 'SERVICE', SG: 'SUNGLASSES',
    SMARTWATCH: 'SMARTWATCH', SMARTWATCHES: 'SMARTWATCH',
    SMART_WATCH: 'SMARTWATCH', SMTWT: 'SMARTWATCH', SPECTACLE: 'SPECTACLE',
    SPECTACLE_FRAME: 'FRAME', SPECTACLE_LENS: 'LENS',
    SPECTACLE_LENSES: 'LENS', SUNGLASS: 'SUNGLASSES',
    SUNGLASSES: 'SUNGLASSES', SVC: 'SERVICE', WATCH: 'WATCH',
    WATCHES: 'WATCH', WRIST_WATCHES: 'WATCH', WT: 'WATCH',
  },
  hsn_by_category: {
    ACC: '392690', ACCESSORIES: '392690', ACCESSORY: '392690',
    CCL: '900130', CK: '910500', CL: '900130',
    COLORED_CONTACT_LENS: '900130', COLORED_CONTACT_LENSES: '900130',
    COLOUR_CONTACTS: '900130', COMPLETE_SPECTACLE: '900490',
    CONSULT: '9993', CONSULTATION: '9993', CONTACT_LENS: '900130',
    CONTACT_LENSES: '900130', EYEGLASS_FRAME: '900311',
    EYEGLASS_LENS: '900150', EYE_CHECKUP: '9993', EYE_EXAM: '9993',
    EYE_TEST: '9993', FR: '900311', FRAME: '900311', FRAMES: '900311',
    HA: '902140', HEARING_AID: '902140', HEARING_AIDS: '902140',
    LENS: '900150', LENSES: '900150', LS: '900150', OPTICAL_LENS: '900150',
    OPTICAL_LENSES: '900150', OPTOMETRY: '9993', READING_GLASSES: '900490',
    RG: '900490', RX_LENSES: '900150', SERVICE: '998599',
    SERVICES: '998599', SG: '900410', SMARTGLASSES: '852580',
    SMARTWATCH: '910221', SMARTWATCHES: '910221', SMTFR: '852580',
    SMTSG: '852580', SMTWT: '910221', SPECTACLE: '900490',
    SPECTACLE_FRAME: '900311', SPECTACLE_LENS: '900150',
    SPECTACLE_LENSES: '900150', SUNGLASS: '900410', SUNGLASSES: '900410',
    SVC: '998599', WALL_CLOCK: '910500', WALL_CLOCKS: '910500',
    WATCH: '910111', WRIST_WATCHES: '910111', WT: '910111',
  },
  rate_by_category: {
    ACC: 18, ACCESSORIES: 18, ACCESSORY: 18, CCL: 5, CK: 18, CL: 5,
    COLORED_CONTACT_LENS: 5, COLORED_CONTACT_LENSES: 5, COLOUR_CONTACTS: 5,
    COMPLETE_SPECTACLE: 5, CONSULT: 0, CONSULTATION: 0, CONTACT_LENS: 5,
    CONTACT_LENSES: 5, EYEGLASS_FRAME: 5, EYEGLASS_LENS: 5,
    EYE_CHECKUP: 0, EYE_EXAM: 0, EYE_TEST: 0, FR: 5, FRAME: 5, FRAMES: 5,
    HA: 0, HEARING_AID: 0, HEARING_AIDS: 0, LENS: 5, LENSES: 5, LS: 5,
    OPTICAL_LENS: 5, OPTICAL_LENSES: 5, OPTOMETRY: 0, READING_GLASSES: 5,
    RG: 5, RX_LENSES: 5, SERVICE: 18, SERVICES: 18, SG: 18,
    SMARTGLASSES: 18, SMARTWATCH: 18, SMARTWATCHES: 18, SMTFR: 18,
    SMTSG: 18, SMTWT: 18, SPECTACLE: 5, SPECTACLE_FRAME: 5,
    SPECTACLE_LENS: 5, SPECTACLE_LENSES: 5, SUNGLASS: 18, SUNGLASSES: 18,
    SVC: 18, WALL_CLOCK: 18, WALL_CLOCKS: 18, WATCH: 18, WRIST_WATCHES: 18,
    WT: 18,
  },
};

// Same maps, but two master rows carry sentinels no real GST rate ever takes.
// CONTACT_LENS: the DELETED frontend hint map sent COLORED_CONTACT_LENSES to
// CONTACT_LENS while the server's sends it to COLORED_CONTACT_LENS -- if that
// copy comes back, the hint test below reads 12345 and dies. LENS: the master
// is keyed by category_hint, not by the spine's category name, so reaching the
// row for an OPTICAL_LENS at all depends on the hint step running.
const SENTINEL = {
  ...SERVER,
  by_cat: { ...SERVER.by_cat, CONTACT_LENS: 12345, LENS: 4321 },
};

/** The pre-deploy response: the endpoint main serves TODAY, which has neither
 *  of the two maps this change introduced. The frontend ships on Vercel before
 *  the backend ships on Railway, so every browser sees exactly this for the
 *  minutes in between. */
const PRE_DEPLOY = {
  by_hsn: SERVER.by_hsn, by_cat: SERVER.by_cat, category_hint: SERVER.category_hint,
};

async function load(withServerData: boolean, payload: object = SERVER) {
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
  beforeEach(() => {
    vi.resetModules();
    STORE.clear();
  });

  it('gives smartglasses 852580, the code the server holds -- not 900410', async () => {
    const { runtime } = await load(true);
    expect(runtime.resolveHsn('SMARTGLASSES')).toBe('852580');
    expect(runtime.resolveHsn('SMTSG')).toBe('852580');
    // Genuine sunglasses still get 900410, so the two are distinguishable.
    expect(runtime.resolveHsn('SUNGLASS')).toBe('900410');
  });

  it('normalises the spelling a legacy row is written in, TRIM included', async () => {
    const { runtime } = await load(true);
    // ONE normaliser for the app: utils/categoryNormalize.canonicalCategory.
    // This file used to inline its own upper-snake regex, which is the same
    // rule MINUS the trim the server does (gst_rates._normalize_category opens
    // with .strip()), so a padded legacy row normalised to '__FRAME__',
    // matched nothing, and printed a BLANK HSN on the tax invoice.
    expect(runtime.resolveHsn('  FRAME  ')).toBe('900311');
    expect(runtime.resolveHsn('Contact Lens')).toBe('900130');
    expect(runtime.resolveHsn('wall clock')).toBe('910500');
    expect(runtime.resolveHsn('optical-lens')).toBe('900150');
    // ...and the shared alias table comes with it: these are spellings the
    // inline regex left untranslated.
    expect(runtime.resolveHsn('SVC')).toBe('998599');
    expect(runtime.resolveHsn('ACCESSORY')).toBe('392690');
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
  beforeEach(() => localStorage.clear());

  it('routes COLORED_CONTACT_LENSES where the SERVER says, not where the old copy said', async () => {
    const { runtime } = await load(true, SENTINEL);
    // Server hint: -> COLORED_CONTACT_LENS, which the master has no row for, so
    // the canonical category table answers 5. Deleted copy: -> CONTACT_LENS
    // -> 12345.
    expect(runtime.resolveGstRate('COLORED_CONTACT_LENSES')).toBe(5);
    // A plain contact lens still reads the master row, so the sentinel is live
    // and this fixture really can tell the two routes apart.
    expect(runtime.resolveGstRate('CONTACT_LENS')).toBe(12345);
  });

  it('reaches a master row that is keyed by the HINT, not by the category name', async () => {
    const { runtime } = await load(true, SENTINEL);
    // The owner edits Settings -> HSN & GST Rates; that row's category_hint is
    // 'LENS'. Products are stored as 'OPTICAL_LENS'. Without the hint step the
    // edit never reaches the screen: the lookup misses and the canonical table
    // answers with the unedited 5%, so the cashier quotes one rate while the
    // server bills another.
    expect(runtime.resolveGstRate('OPTICAL_LENS')).toBe(4321);
    expect(runtime.resolveGstRate('LS')).toBe(4321);
  });

  it('takes a canonical rate off the SERVER table before its own offline copy', async () => {
    const { runtime } = await load(true, {
      ...SERVER,
      rate_by_category: { ...SERVER.rate_by_category, SMARTGLASSES: 12345 },
    });
    // Smartglasses have no editable-master row (852580 is not in by_hsn and
    // SMARTGLASSES is not a category_hint), so the CANONICAL table is what
    // answers. The offline copy in constants/gst.ts says 18. Reading 12345
    // proves an edit to gst_rates.GST_CATEGORY_TABLE reaches a running browser
    // with no frontend release -- the entire point of serving the table, and
    // invisible while the two copies happen to agree.
    expect(runtime.resolveGstRate('SMARTGLASSES')).toBe(12345);
    // ...and a category the master DOES cover still comes off the master.
    expect(runtime.resolveGstRate('FRAME')).toBe(5);
  });
});

describe('THE POS CONSTRAINT: no category is quoted a rate the server denies', () => {
  // The whole table, not a sample. resolveGstRate() is what the cart preview
  // and the printed tax invoice show; the server is what the customer is
  // actually charged (orders.py -> gst_rates.resolve_gst_rate). A category
  // where the two differ is a wrong percentage on a Rule-46 document.
  const NAMED = Object.entries(SERVER.rate_by_category) as Array<[string, number]>;
  const disagreements = (rate: (c: string) => number) =>
    NAMED.filter(([cat, r]) => rate(cat) !== r)
         .map(([cat, r]) => [cat, rate(cat), 'server bills', r]);

  beforeEach(() => localStorage.clear());

  it('quotes the server rate for all 55 named categories BEFORE the endpoint answers', async () => {
    const { runtime } = await load(false);
    expect(NAMED.length).toBe(55);
    // Main quoted a rate the server denies on 19 of these, cold: the six
    // eye-test spellings (18% vs 0%), SPECTACLE_FRAME / LENSES /
    // OPTICAL_LENSES / SPECTACLE_LENS / SPECTACLE_LENSES /
    // COLORED_CONTACT_LENSES / CCL (18% vs 5%), and every unrecognised
    // spelling (18% vs the 5% default).
    expect(disagreements((c) => runtime.resolveGstRate(c))).toEqual([]);
  });

  it('quotes the server rate for all 55 named categories AFTER it answers', async () => {
    const { runtime } = await load(true);
    // Main was wrong on 9 of these loaded: ACCESSORY and SVC (5% vs 18%) and
    // the six eye-test spellings (5% vs 0%).
    expect(disagreements((c) => runtime.resolveGstRate(c))).toEqual([]);
  });

  it('rates an unrecognised category at the 5% the server defaults to, in BOTH states', async () => {
    // gst_rates.DEFAULT_GST_RATE is 5.0 -- moved 18 -> 5 on 2026-05-28 after a
    // QA finding that an uncategorised product billed 18%. The frontend still
    // said 18. On an optical chain's invoice that is a 13-point over-charge on
    // a line the server charges 5% for.
    for (const withServer of [true, false]) {
      STORE.clear();
      const { runtime } = await load(withServer);
      for (const junk of ['', 'UNCATEGORIZED', 'Fastrack P357BK1', 'FOOBAR']) {
        expect([withServer, junk, runtime.resolveGstRate(junk)]).toEqual([withServer, junk, 5]);
      }
    }
  });
});

describe('the day the frontend ships before the backend', () => {
  it('keeps the server tables when the endpoint answers in its PRE-DEPLOY shape', async () => {
    STORE.clear();
    // Session 1: the new endpoint answers, and the tables land in localStorage.
    await load(true);
    // Session 2, same browser, but Vercel has the new bundle and Railway has
    // not: a 200 with no hsn_by_category and no rate_by_category at all.
    // Taking `|| {}` from that response would blank both maps on a LIVE POS and
    // then write the blank over the good snapshot, so every invoice line with
    // no stored HSN prints an empty HSN cell until Railway catches up.
    const { runtime } = await load(true, PRE_DEPLOY);
    expect(runtime.resolveHsn('SMTSG')).toBe('852580');
    expect(runtime.resolveGstRate('EYE_TEST')).toBe(0);
    // The tables the OLD response does carry are still the ones that win.
    expect(runtime.resolveGstRate('FRAME')).toBe(5);
  });
});

describe('the HSN dropdown can show every code the resolver sets', () => {
  it('offers 852580 and 9993, which no local list has', async () => {
    STORE.clear();
    const { runtime } = await load(true);
    const codes = runtime.hsnOptions().map((o) => o.value);
    // QuickAddPage sets the REQUIRED HSN field from resolveHsn(category). A
    // value with no matching <option> renders the field BLANK, on the two
    // categories that need it most: smartglasses (35 of 68 live products) and
    // eye tests.
    expect(runtime.resolveHsn('SMTSG')).toBe('852580');
    expect(codes).toContain('852580');
    expect(codes).toContain('9993');
    // Every code the server can hand a category is offerable, not just those two.
    const settable = new Set(Object.values(SERVER.hsn_by_category));
    expect([...settable].filter((c) => !codes.includes(c))).toEqual([]);
    // The hand-written codes a cataloguer may still pick are not dropped.
    expect(codes).toContain('900319');   // metal frames -- local list only
    // ...and an option never quotes a rate the resolver denies.
    const smart = runtime.hsnOptions().find((o) => o.value === '852580');
    expect(smart?.gstRate).toBe(18);
    expect(smart?.label).toContain('18%');
  });

  it('quotes the OWNER-edited master rate on a code, not the hand-written one', async () => {
    STORE.clear();
    const { runtime } = await load(true, {
      ...SERVER, by_hsn: { ...SERVER.by_hsn, '900410': 12345 },
    });
    // Picking an option also writes gst_rate on the new product (QuickAddPage).
    // constants/gst.ts has 900410 hand-written at 18%; Settings -> HSN & GST
    // Rates is where the owner changes a rate without a release, so the master
    // has to win here too.
    const sun = runtime.hsnOptions().find((o) => o.value === '900410');
    expect(sun?.gstRate).toBe(12345);
    // A code the master has no row for keeps its offline rate.
    expect(runtime.hsnOptions().find((o) => o.value === '900319')?.gstRate).toBe(5);
  });
});

describe('a cloned product is rated by its category, not by a hand-written 18', () => {
  it('fills a missing gst_rate from the category, in BOTH states', async () => {
    // A doc old enough to carry no gst_rate. The clone form's rate is saved as
    // the new SKU's gst_rate (an explicit rate beats the server's own), and
    // QuickAddPage deliberately SKIPS the category autofill when the clone
    // brings an hsn_code -- so the default here rides onto the product.
    const legacy = { category: 'FRAME', hsn_code: '900311', attributes: {} };
    for (const withServer of [true, false]) {
      STORE.clear();
      const { shared } = await load(withServer);
      expect([withServer, shared.productToFormValues(legacy as never).gstRate])
        .toEqual([withServer, '5']);
    }
  });
});

describe('one product, one spelling of its HSN', () => {
  it('sends no hsn_code rather than an 8-digit contact-lens code', async () => {
    STORE.clear();
    const { shared } = await load(false);
    const payload = shared.buildProductPayload({
      category: 'CL', attributes: { brand_name: 'Acuvue', model_no: 'OASYS' },
      hsnCode: '', gstRate: '5', mrp: '1200', offerPrice: '', discountCategory: '',
      syncToShopify: false, shopifyTags: [], publishPOS: true,
    } as never);
    // '90013000' and '900130' are the same HSN spelled two ways. Two spellings
    // split one product across two rows of the invoice's HSN-wise summary and
    // of GSTR-1. Omitting it lets the server write its own single spelling.
    expect(payload.hsn_code).toBeUndefined();
  });
});

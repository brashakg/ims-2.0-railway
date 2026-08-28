// ============================================================================
// Settings screens render HSN/GST from the SERVER, never a hand-typed table
// ============================================================================
// settingsTypes.CATEGORY_DEFINITIONS used to carry hand-typed hsnCode/gstRate
// columns that the SettingsStore Category Master rendered as fact -- and they
// contradicted GET /products/gst-rates on 7 of 13 rows (SMTSG/SMTFR said
// 900490 where the server says 852580; SMTWT 8517 vs 910221; ACC 9004 vs
// 392690; SVC 9987 vs 998599; HA 5% vs 0%; CL an 8-digit spelling). The
// columns are DELETED and the screen reads gstRuntime's server maps.
//
// FIXTURES ARE CHOSEN SO THE TWO SIDES DISAGREE: every assertion below turns
// on a value the deleted table got wrong (852580, 902140/0%, 910221, 392690,
// 998599, 900130). A fixture where both agreed could not tell which one the
// screen read. The endpoint-not-answered state is asserted explicitly: the
// screen must degrade to an em-dash, never to a hand-typed value.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockGet = vi.fn();
vi.mock('../../../services/api/client', () => ({ default: { get: (...a: unknown[]) => mockGet(...a) } }));

const mockHsnList = vi.fn();
vi.mock('../../../services/api/hsn', () => ({ hsnApi: { list: (...a: unknown[]) => mockHsnList(...a) } }));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: () => {}, error: () => {}, warning: () => {}, info: () => {} }),
}));

// SettingsStore's OTHER sections pull the services barrel; keep the graph small.
vi.mock('../../../services/api', () => ({
  adminBrandApi: { getBrands: async () => ({ brands: [] }) },
  adminDiscountApi: { getEnforcedDiscountCaps: async () => ({}) },
}));

// A REAL localStorage (the runner's global has no methods; see
// constants/__tests__/gstServerFed.test.ts for the full story).
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

// What GET /products/gst-rates answers for the paths the 13 category codes
// take: the seeded master (by_hsn/by_cat), the canonical category->HSN and
// category->rate tables, and the hint vocabulary -- plus ZZ (a spelling) ->
// ZZ_SENTINEL_HINT, a hint bucket the deleted hand list never contained, so
// the Category select can only show it by reading the server.
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
    ACCESSORIES: 'ACCESSORIES', COLORED_CONTACT_LENS: 'COLORED_CONTACT_LENS',
    CONTACT_LENS: 'CONTACT_LENS', FRAME: 'FRAME', HEARING_AID: 'HEARING_AID',
    OPTICAL_LENS: 'LENS', READING_GLASSES: 'SPECTACLE', SERVICES: 'SERVICE',
    SMARTWATCH: 'SMARTWATCH', SUNGLASS: 'SUNGLASSES', WATCH: 'WATCH',
    ZZ: 'ZZ_SENTINEL_HINT',
  },
  hsn_by_category: {
    ACCESSORIES: '392690', CONTACT_LENS: '900130', FRAME: '900311',
    HEARING_AID: '902140', OPTICAL_LENS: '900150', READING_GLASSES: '900490',
    SERVICES: '998599', SMARTGLASSES: '852580', SMARTWATCH: '910221',
    SUNGLASS: '900410', WALL_CLOCK: '910500', WATCH: '910111',
  },
  rate_by_category: {
    ACCESSORIES: 18, CONTACT_LENS: 5, FRAME: 5, HEARING_AID: 0,
    OPTICAL_LENS: 5, READING_GLASSES: 5, SERVICES: 18, SMARTGLASSES: 18,
    SMARTWATCH: 18, SUNGLASS: 18, WALL_CLOCK: 18, WATCH: 18,
  },
};

async function loadScreens(withServerData: boolean) {
  vi.resetModules();
  STORE.clear();
  mockGet.mockReset();
  mockHsnList.mockReset();
  if (withServerData) mockGet.mockResolvedValue({ data: SERVER });
  else mockGet.mockRejectedValue(new Error('endpoint not answered'));
  mockHsnList.mockResolvedValue({ hsn_rates: [] });
  const { CategorySection } = await import('../SettingsStore');
  const { HsnRatesSection } = await import('../../../components/settings/HsnRatesSection');
  return { CategorySection, HsnRatesSection };
}

describe('Category Master shows the HSN/GST the SERVER holds', () => {
  it('renders the server values the deleted hand-typed columns contradicted', async () => {
    const { CategorySection } = await loadScreens(true);
    render(<CategorySection />);

    // Smart Sunglass + Smart Glasses: the hand column said 900490.
    expect(await screen.findAllByText('HSN: 852580 | GST: 18%')).toHaveLength(2);
    // Hearing Aid: hand column said HSN 9021 at 5%; the server bills 0%.
    expect(screen.getByText('HSN: 902140 | GST: 0%')).toBeInTheDocument();
    // Contact Lens: hand column carried the 8-digit spelling 90013000.
    expect(screen.getByText('HSN: 900130 | GST: 5%')).toBeInTheDocument();
    // Smart Watch 8517 -> 910221, Accessories 9004 -> 392690, Service 9987 -> 998599.
    expect(screen.getByText('HSN: 910221 | GST: 18%')).toBeInTheDocument();
    expect(screen.getByText('HSN: 392690 | GST: 18%')).toBeInTheDocument();
    expect(screen.getByText('HSN: 998599 | GST: 18%')).toBeInTheDocument();
    // And none of the old wrong lines survive anywhere on the screen.
    expect(screen.queryByText('HSN: 8517 | GST: 18%')).toBeNull();
    expect(screen.queryByText('HSN: 9021 | GST: 5%')).toBeNull();
    expect(screen.queryByText('HSN: 90013000 | GST: 5%')).toBeNull();
  });

  it('degrades to an em-dash when the endpoint has not answered -- never a hand-typed value', async () => {
    const { CategorySection } = await loadScreens(false);
    render(<CategorySection />);

    // All 13 rows show the honest placeholder...
    expect(await screen.findAllByText('HSN: — | GST: —')).toHaveLength(13);
    // ...and no HSN or rate is invented, neither the server's nor the old copy's.
    expect(screen.queryByText(/HSN: \d/)).toBeNull();
    expect(screen.queryByText(/GST: \d/)).toBeNull();
  });
});

describe('HSN & GST Rates: the Category select offers the SERVER hint vocabulary', () => {
  it('lists a hint bucket only the server names, so the list cannot be a stale copy', async () => {
    const { HsnRatesSection } = await loadScreens(true);
    render(<HsnRatesSection />);
    await screen.findByText('No HSN rates configured.');

    fireEvent.click(screen.getByRole('button', { name: 'Add HSN' }));
    // The deleted hand list could never have shown this bucket.
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'ZZ_SENTINEL_HINT' })).toBeInTheDocument(),
    );
    expect(screen.getByRole('option', { name: 'COLORED_CONTACT_LENS' })).toBeInTheDocument();
    // 12 server hints + the '—' empty option.
    expect(screen.getAllByRole('option')).toHaveLength(13);
  });

  it('offers nothing before the endpoint answers -- never the hand-typed list', async () => {
    const { HsnRatesSection } = await loadScreens(false);
    render(<HsnRatesSection />);
    await screen.findByText('No HSN rates configured.');

    fireEvent.click(screen.getByRole('button', { name: 'Add HSN' }));
    // Only the '—' empty option; CONTACT_LENS etc. must NOT be invented locally.
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.queryByRole('option', { name: 'CONTACT_LENS' })).toBeNull();
  });
});

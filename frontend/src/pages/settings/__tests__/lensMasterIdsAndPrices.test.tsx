// L1-F07 / L1-F08 (audit 2026-11-04): the legacy Lens Master lists.
// F07: the server keys rows brand_id / index_id / coating_id / addon_id, the
// screen read `.id`, so every rename/delete hit `/undefined` (404). The index
// label lives in `description`, the screen read `name`.
// F08: a malformed price ("banana") was sent as 0 and created a free item.
// The real adminLensApi runs here; only the HTTP client is faked.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../../../services/api/client';
import { LensMasterSection } from '../SettingsLens';

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;
const put = api.put as unknown as ReturnType<typeof vi.fn>;
const del = api.delete as unknown as ReturnType<typeof vi.fn>;

const LISTS: Record<string, unknown> = {
  '/admin/lens/brands': { brands: [{ brand_id: 'LB-1', name: 'Zeiss', code: 'ZEISS' }, { brand_id: 'LB-2', name: 'Hoya', code: 'HOYA' }] },
  '/admin/lens/indices': { indices: [{ index_id: 'LI-1', value: '1.60', multiplier: 1.2, description: 'Thin' }] },
  '/admin/lens/coatings': { coatings: [{ coating_id: 'LC-1', name: 'Blue Cut', code: 'BLUE_CUT', price: 500 }] },
  '/admin/lens/addons': { addons: [{ addon_id: 'LA-1', name: 'Tint', code: 'TINT', price: 300, type: 'ADDON' }] },
};

beforeEach(() => {
  vi.clearAllMocks();
  get.mockImplementation(async (url: string) => ({ data: LISTS[url] ?? {} }));
  post.mockResolvedValue({ data: {} });
  put.mockResolvedValue({ data: {} });
  del.mockResolvedValue({ data: {} });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('Lens Master rows act on their real ids (L1-F07)', () => {
  it('rename and delete send the stored id of the clicked row, never /undefined', async () => {
    render(<LensMasterSection />);
    await screen.findByText('Hoya');
    vi.spyOn(window, 'prompt').mockReturnValue('Hoya Vision');
    fireEvent.click(screen.getByRole('button', { name: 'Rename lens brand Hoya' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/admin/lens/brands/LB-2', { name: 'Hoya Vision' }));

    fireEvent.click(screen.getByRole('button', { name: 'Delete lens index 1.60' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete coating Blue Cut' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete add-on Tint' }));
    await waitFor(() => expect(del).toHaveBeenCalledTimes(3));
    expect(del.mock.calls.map((c) => c[0])).toEqual([
      '/admin/lens/indices/LI-1', '/admin/lens/coatings/LC-1', '/admin/lens/addons/LA-1',
    ]);
    for (const c of [...put.mock.calls, ...del.mock.calls]) expect(String(c[0])).not.toContain('undefined');
  });

  it('shows the index description as its label', async () => {
    render(<LensMasterSection />);
    expect(await screen.findByText('Thin')).toBeInTheDocument();
  });
});

describe('Lens Master prices are validated, never turned into 0 (L1-F08)', () => {
  const addCoating = async (answers: Array<string | null>) => {
    render(<LensMasterSection />);
    await screen.findByText('Blue Cut');
    const prompt = vi.spyOn(window, 'prompt');
    answers.forEach((a) => prompt.mockReturnValueOnce(a));
    fireEvent.click(screen.getByRole('button', { name: /Add Coating/ }));
    await new Promise((r) => setTimeout(r, 0));
  };

  it.each(['banana', '  ', '-5', 'NaN', '12abc', '1e3', 'Infinity'])('refuses price %j', async (bad) => {
    await addCoating(['AUDIT BADPRICE', bad]);
    expect(post).not.toHaveBeenCalled();
  });

  it('cancelling the price prompt stops the whole add', async () => {
    await addCoating(['AUDIT CANCEL', null]);
    expect(post).not.toHaveBeenCalled();
  });

  it.each([['0', 0], ['250', 250], [' 99.50 ', 99.5]])('keeps a deliberate price %j', async (raw, want) => {
    await addCoating(['AUDIT OK', raw]);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toMatchObject({ name: 'AUDIT OK', price: want });
  });

  it('the add-on door refuses a malformed price too', async () => {
    render(<LensMasterSection />);
    await screen.findByText('Tint');
    vi.spyOn(window, 'prompt').mockReturnValueOnce('AUDIT ADDON').mockReturnValueOnce('banana');
    fireEvent.click(screen.getByRole('button', { name: /Add Add-on/ }));
    await new Promise((r) => setTimeout(r, 0));
    expect(post).not.toHaveBeenCalled();
  });
});

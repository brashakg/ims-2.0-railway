// ============================================================================
// The Needs-review drawer's Restore (round 4, 2026-10-13)
// ============================================================================
// Only THE restore door (POST /catalog/products/bulk-restore) brings an
// archived (deleted) product back -- the server refuses a plain switch-on of
// one (409 'This product is archived - use Restore'). A product merely
// switched off ("Not for POS", no archive) is still an ordinary switch.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { roles: ['ADMIN'] as string[] };
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-me', roles: auth.roles },
    hasRole: (r: string | string[]) => (Array.isArray(r) ? r : [r]).some((x) => auth.roles.includes(x)),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../services/api/onlineStore', () => ({ pushApi: { pushProduct: vi.fn() } }));
vi.mock('../../../services/api/catalog', () => ({
  catalogProductsApi: {
    get: vi.fn(),
    update: vi.fn(async () => ({})),
    promote: vi.fn(),
    promoteDryRun: vi.fn(async () => ({ ok: false, gaps: [], duplicate_warnings: [] })),
  },
  catalogBulkApi: { restore: vi.fn() },
}));
vi.mock('../productAddShared', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../productAddShared')>()),
  loadCategoryRegistry: vi.fn(async () => undefined),
}));

import { catalogBulkApi, catalogProductsApi } from '../../../services/api/catalog';
import { CatalogProductDrawer } from '../CatalogProductDrawer';

const restore = catalogBulkApi.restore as unknown as ReturnType<typeof vi.fn>;
const update = catalogProductsApi.update as unknown as ReturnType<typeof vi.fn>;
const get = catalogProductsApi.get as unknown as ReturnType<typeof vi.fn>;

const IMPORT = {
  id: 'T-IMP',
  sku: 'IMP-1',
  title: 'Imported frame',
  category: 'FRAME',
  needs_review: true,
  is_active: false,
};

function open(doc: Record<string, unknown>) {
  const onUpdated = vi.fn();
  render(
    <MemoryRouter>
      <CatalogProductDrawer item={{ kind: 'imported', doc }} onClose={() => {}} onUpdated={onUpdated} />
    </MemoryRouter>
  );
  return onUpdated;
}

beforeEach(() => {
  auth.roles = ['ADMIN'];
  restore.mockReset();
  update.mockClear();
  get.mockReset();
  get.mockResolvedValue({ ...IMPORT, is_active: true, deleted_at: null });
  Object.values(toast).forEach((f) => f.mockClear());
});

describe('the drawer Restore', () => {
  it('an archived import comes back through the restore door, never a switch-on', async () => {
    restore.mockResolvedValue({ applied: true, results: [{ product_id: 'T-IMP', status: 'done', switched_on: true }] });
    const onUpdated = open({ ...IMPORT, deleted_at: '2026-10-12T05:00:00+00:00' });
    fireEvent.click(screen.getByRole('button', { name: /Restore/ }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    expect(restore).toHaveBeenCalledWith(['T-IMP']);
    expect(update).not.toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith('Restored to the review queue.');
  });

  it('says why when it comes back switched off', async () => {
    restore.mockResolvedValue({
      applied: true,
      results: [{ product_id: 'T-IMP', status: 'done', switched_on: false,
        note: 'Back in the catalogue switched off, as it was before it was archived.' }],
    });
    open({ ...IMPORT, deleted_at: '2026-10-12T05:00:00+00:00' });
    fireEvent.click(screen.getByRole('button', { name: /Restore/ }));
    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith(
        'Restored. Back in the catalogue switched off, as it was before it was archived.'
      )
    );
  });

  it("a refusal shows the door's own reason", async () => {
    restore.mockResolvedValue({
      applied: true,
      results: [{ product_id: 'T-IMP', status: 'refused', reason: 'Not archived' }],
    });
    open({ ...IMPORT, deleted_at: '2026-10-12T05:00:00+00:00' });
    fireEvent.click(screen.getByRole('button', { name: /Restore/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Not archived'));
    expect(get).not.toHaveBeenCalled();
  });

  it('a product only switched off (Not for POS) is switched back on as before', async () => {
    open({ ...IMPORT });
    fireEvent.click(screen.getByRole('button', { name: /Restore/ }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('T-IMP', { is_active: true }));
    expect(restore).not.toHaveBeenCalled();
  });

  it('Archive (Not for POS) switches an active import off', async () => {
    open({ ...IMPORT, is_active: true });
    fireEvent.click(screen.getByRole('button', { name: /Archive/ }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('T-IMP', { is_active: false }));
  });

  it('an archived import offers no Approve for POS (only Restore brings it back)', () => {
    for (const roles of [['ADMIN'], ['CATALOG_MANAGER']]) {
      auth.roles = roles;
      const { unmount } = render(
        <MemoryRouter>
          <CatalogProductDrawer
            item={{ kind: 'imported', doc: { ...IMPORT, is_active: true, deleted_at: '2026-10-12T05:00:00+00:00' } }}
            onClose={() => {}}
          />
        </MemoryRouter>
      );
      expect(screen.queryByRole('button', { name: /Approve for POS/ })).toBeNull();
      unmount();
    }
    open({ ...IMPORT, is_active: true });
    expect(screen.getByRole('button', { name: /Approve for POS/ })).toBeTruthy();
  });

  it('only an admin is offered Restore on an archived product (the door is theirs)', () => {
    auth.roles = ['CATALOG_MANAGER'];
    open({ ...IMPORT, deleted_at: '2026-10-12T05:00:00+00:00' });
    expect(screen.queryByRole('button', { name: /Restore/ })).toBeNull();
  });
});

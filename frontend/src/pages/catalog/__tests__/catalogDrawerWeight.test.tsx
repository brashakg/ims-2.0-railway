// ============================================================================
// The Needs-review drawer's Weight box (owner 2026-10-22)
// ============================================================================
// Cataloguers fix imported products in the drawer's quick-fix form. Weight
// (grams, a top-level field Shopify ships by) had no box there -- only the
// full "Edit everything" page carried it.

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

import { catalogProductsApi } from '../../../services/api/catalog';
import { CatalogProductDrawer } from '../CatalogProductDrawer';


const update = catalogProductsApi.update as unknown as ReturnType<typeof vi.fn>;
const get = catalogProductsApi.get as unknown as ReturnType<typeof vi.fn>;

const IMPORT = { id: 'T-IMP', sku: 'IMP-1', title: 'Imported sunglass', category: 'SUNGLASS', needs_review: true };

function open(doc: Record<string, unknown>) {
  render(
    <MemoryRouter>
      <CatalogProductDrawer item={{ kind: 'imported', doc }} onClose={() => {}} onUpdated={vi.fn()} />
    </MemoryRouter>
  );
}

beforeEach(() => {
  auth.roles = ['ADMIN'];
  update.mockClear();
  get.mockReset();
  get.mockResolvedValue({ ...IMPORT, weight: 28 });
  Object.values(toast).forEach((f) => f.mockClear());
});

describe('the drawer Weight box', () => {
  it('shows the saved weight and sends a changed weight in grams', async () => {
    open({ ...IMPORT, weight: 25 });
    const box = screen.getByTitle('Weight (g)') as HTMLInputElement;
    expect(box.value).toBe('25');
    fireEvent.change(box, { target: { value: '28' } });
    fireEvent.click(screen.getByRole('button', { name: /Save fixes/ }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toMatchObject({ weight: 28 });
  });

  it('an untouched weight is not re-sent', async () => {
    open({ ...IMPORT, weight: 25 });
    fireEvent.click(screen.getByRole('button', { name: /Save fixes/ }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Nothing changed yet.'));
    expect(update).not.toHaveBeenCalled();
  });
});

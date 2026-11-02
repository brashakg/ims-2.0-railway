// ============================================================================
// "It's a different product" is shown where products are reviewed
// ============================================================================
// Owner 2026-11-01: anyone who can add products may say the same model
// written differently (RB4340 vs 4340) is a different product, with a
// reason; the server records who and when, and the drawer shows it.

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-me', roles: ['ADMIN'] }, hasRole: () => true }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../services/api/onlineStore', () => ({ pushApi: { pushProduct: vi.fn() } }));
vi.mock('../../../services/api/catalog', () => ({
  catalogProductsApi: { get: vi.fn(), update: vi.fn(), promote: vi.fn(), promoteDryRun: vi.fn() },
  catalogBulkApi: { restore: vi.fn() },
}));
vi.mock('../ModelFamilyPanel', () => ({ ModelFamilyPanel: () => null }));
vi.mock('../productAddShared', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../productAddShared')>()),
  loadCategoryRegistry: vi.fn(async () => undefined),
}));

import { CatalogProductDrawer } from '../CatalogProductDrawer';

describe('the product drawer', () => {
  it('shows who marked it a different product, from which one, and why', () => {
    render(
      <MemoryRouter>
        <CatalogProductDrawer
          item={{
            kind: 'spine',
            doc: {
              product_id: 'P-NEW',
              sku: 'SG-RAYBAN-4340-601/M3-50',
              brand: 'Ray-Ban',
              model: '4340',
              category: 'SUNGLASS',
              distinct_from: [
                {
                  product_id: 'P-OLD',
                  sku: 'SG-RAYBAN-RB4340-601/M3-50',
                  reason: 'Junior / kids version',
                  by_name: 'priya',
                  at: '2026-11-01T12:00:00Z',
                },
              ],
            },
          }}
          onClose={() => {}}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('distinct-from')).toHaveTextContent(
      'Not the same as SG-RAYBAN-RB4340-601/M3-50: Junior / kids version (by priya, 2026-11-01)',
    );
  });
});

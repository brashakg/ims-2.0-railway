// ============================================================================
// Reviewing an imported product shows the "model number repeats the brand"
// hint too (#1271): prod 10-24 had 7 products saved as 'CARRERA 3001/S', and
// imports are where such a model number comes in. The hint never blocks.
// ============================================================================

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Cat', roles: ['CATALOG_MANAGER'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => {
      throw new Error('offline');
    }),
    getBrandOptions: vi.fn(async () => ({ brands: [] })),
    getProduct: vi.fn(async () => ({})),
    updateProduct: vi.fn(async () => ({})),
    previewSku: vi.fn(async () => ({ category: 'SUNGLASS', sku: 'SG-CARRERA-CARRERA3001S-086HA-54' })),
    getWebsiteVerdict: vi.fn(async (brand: string) => ({ brand, online: true, reason: null })),
  },
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error {
    status = 0;
  },
  catalogProductsApi: {
    get: vi.fn(async () => ({
      id: 'CAT-IMPORT',
      sku: 'BVI-1',
      category: 'SUNGLASS',
      needs_review: true,
      attributes: { brand_name: 'Carrera', model_no: 'CARRERA 3001/S', colour_code: '086HA', lens_size: '54' },
    })),
    list: vi.fn(async () => ({ products: [], total: 0 })),
    promoteDryRun: vi.fn(async () => ({ ok: true, gaps: [], duplicate_warnings: [] })),
  },
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));

import { QuickAddPage } from '../QuickAddPage';

beforeEach(() => {
  window.sessionStorage.clear();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
});

describe('import review', () => {
  it('warns that the model number repeats the brand, without the photo hint', async () => {
    render(
      <MemoryRouter initialEntries={['/catalog/add?review=CAT-IMPORT']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    const box = await screen.findByTestId('qa-soft-warnings', {}, { timeout: 5000 });
    expect(box.textContent).toMatch(/repeats the brand/);
    expect(box.textContent).not.toMatch(/photo/i);
  });
});

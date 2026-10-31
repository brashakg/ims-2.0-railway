import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { QuickAddForm } from '../useQuickAddForm';

const suggestColourName = vi.fn();
vi.mock('../../../../services/api/products', () => ({
  productApi: {
    suggestColourName: (...a: unknown[]) => suggestColourName(...a),
    generateDescription: vi.fn(),
  },
}));
vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));

import { IdentitySection } from '../IdentitySection';

function form(category: string, over: Partial<Record<keyof QuickAddForm, unknown>> = {}): QuickAddForm {
  return {
    registryReady: true, selectedCategory: category, setSelectedCategory: vi.fn(),
    attributes: { brand_name: 'Ray-Ban' }, setAttr: vi.fn(),
    description: '', setDescription: vi.fn(), hsnCode: '', setHsnCode: vi.fn(), gstRate: '', setGstRate: vi.fn(),
    hsnMatchesCategory: true, weight: '', setWeight: vi.fn(), storedAttributes: {},
    displayName: '', setDisplayName: vi.fn(), reviewTags: [], setReviewTags: vi.fn(),
    subbrandsByBrand: {}, errors: {}, showAdvanced: false, setShowAdvanced: vi.fn(),
    openSections: { identity: true }, toggleSection: vi.fn(), liveErrors: {}, sectionIssues: { identity: 0 },
    firstFieldRef: { current: null }, editMode: null, isReviewMode: false, variantCtx: null,
    flaggedFields: new Set<string>(), clearFlag: vi.fn(), reviewSnapshot: null,
    handleSimilarPick: vi.fn(), handleSimilarOpen: vi.fn(), handleFamilyFound: vi.fn(), setHintBrandInModel: vi.fn(),
    copyFromLastFields: {}, copyFromLast: vi.fn(), lastSaved: null,
    images: ['/api/v1/products/image/one', '/api/v1/products/image/two'],
    ...over,
  } as unknown as QuickAddForm;
}

const renderSection = (f: QuickAddForm) =>
  render(<MemoryRouter><IdentitySection form={f} /></MemoryRouter>);

describe('IdentitySection wires the Colour Name suggest', () => {
  beforeEach(() => {
    suggestColourName.mockReset();
    suggestColourName.mockResolvedValue({ status: 'NO_PHOTO', suggestion: '', message: 'x' });
  });

  it.each(['SG', 'FR'])('%s shows it', (code) => {
    renderSection(form(code));
    expect(screen.getByTestId('qa-colour-name')).toBeInTheDocument();
  });

  it.each(['CL', 'CCL'])('%s does not (contact lenses keep their own grid field)', (code) => {
    renderSection(form(code));
    expect(screen.queryByTestId('qa-colour-name')).not.toBeInTheDocument();
  });

  it('Add product: posts photo 1 and no product id', () => {
    renderSection(form('SG'));
    fireEvent.click(screen.getByTitle('Suggest a colour name from photo 1'));
    const sent = suggestColourName.mock.calls[0][0];
    expect(sent.image_url).toBe('/api/v1/products/image/one');
    expect(sent).not.toHaveProperty('product_id');
    expect(sent).not.toHaveProperty('catalog_id');
  });

  it('Edit (spine): posts the product id', () => {
    renderSection(form('SG', { editMode: { kind: 'spine', id: 'prod-7', sku: 'S' } }));
    fireEvent.click(screen.getByTitle('Suggest a colour name from photo 1'));
    expect(suggestColourName.mock.calls[0][0]).toMatchObject({
      product_id: 'prod-7', image_url: '/api/v1/products/image/one',
    });
  });

  it('Review (catalog draft): posts the draft id, not a product id', () => {
    renderSection(form('SG', { editMode: { kind: 'catalog', id: 'cat-3', sku: 'S' }, isReviewMode: true }));
    fireEvent.click(screen.getByTitle('Suggest a colour name from photo 1'));
    const sent = suggestColourName.mock.calls[0][0];
    expect(sent).toMatchObject({ catalog_id: 'cat-3' });
    expect(sent).not.toHaveProperty('product_id');
  });

  it('uses the grid caps-entry and amber-flag classes', () => {
    renderSection(form('SG', { flaggedFields: new Set(['colour_name']) }));
    const input = screen.getByLabelText('Colour Name');
    expect(input).toHaveClass('input-field', 'uppercase', 'ring-amber-400');
  });
});

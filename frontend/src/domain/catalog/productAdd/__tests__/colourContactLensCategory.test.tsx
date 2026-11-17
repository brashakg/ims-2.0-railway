// L1-F43: Brand Master had no Colour Contact Lens (CCL) category box, so a
// category-scoped brand could never be offered on the CCL add screen.
// L1-F46: the shared category inference mapped COLORED_CONTACT_LENS to CL, so
// a CCL variant opened locked to Contact Lens and the save was refused.
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({ useAuth: () => ({ user: { roles: ['SUPERADMIN'] } }) }));
vi.mock('../../../../context/ToastContext', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('../../../../services/api', () => ({
  adminBrandApi: { getBrands: vi.fn(async () => ({ brands: [] })) },
  adminDiscountApi: {},
}));

import { inferCategoryCode, inferCategoryFromText } from '../inferCategory';
import { productToVariantFormValues } from '../variantRules';
import { CATEGORY_DEFINITIONS } from '../../../../pages/settings/settingsTypes';
import { BrandSection } from '../../../../pages/settings/SettingsStore';

describe('Colour Contact Lens keeps its own category (L1-F46)', () => {
  it.each(['COLORED_CONTACT_LENS', 'Colored Contact Lens', 'COLOUR_CONTACT_LENS', 'colour contacts', 'CCL'])(
    'infers %j as CCL', (raw) => expect(inferCategoryCode(raw)).toBe('CCL'),
  );
  it('plain contact lenses stay CL', () => {
    expect(inferCategoryCode('CONTACT_LENS')).toBe('CL');
    expect(inferCategoryFromText('Acuvue Oasys contact lens')).toBe('CL');
  });
  it('a colour contact lens title infers CCL', () => {
    expect(inferCategoryFromText('FreshLook colour contact lens Hazel')).toBe('CCL');
  });
  it('a CCL variant seeds the CCL category, not CL', () => {
    const seed = productToVariantFormValues({
      product_id: 'P1', category: 'COLORED_CONTACT_LENS', brand: 'Alcon', model: 'FreshLook',
      attributes: { brand_name: 'Alcon', model_name: 'FreshLook', colour_name: 'Hazel' },
    } as unknown as Parameters<typeof productToVariantFormValues>[0]);
    expect(seed.values.category).toBe('CCL');
  });
});

describe('Brand Master offers Colour Contact Lens (L1-F43)', () => {
  it('lists CCL as its own category, next to CL', () => {
    const codes = CATEGORY_DEFINITIONS.map((c) => c.code);
    expect(codes).toContain('CCL');
    expect(codes).toContain('CL');
  });
  it('the brand form shows a Colour Contact Lens box', async () => {
    render(<MemoryRouter><BrandSection /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add Brand' }));
    const box = screen.getByRole('checkbox', { name: 'Colour Contact Lens' });
    fireEvent.click(box);
    expect(box).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Contact Lens' })).not.toBeChecked();
  });
});

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const { registry, save } = vi.hoisted(() => ({
  registry: Object.freeze({
    code: 'SUNGLASS', sku_prefix: 'SG', name: 'Sunglass',
    fields: [{ name: 'lens_colour', label: 'Lens Colour', required: false }],
  }),
  save: vi.fn(async () => ({ values: ['Green'] })),
}));
vi.mock('../../../services/api/products', () => ({
  productApi: { getCategoryRegistry: vi.fn(async () => ({ categories: [registry] })) },
}));
vi.mock('../../../services/api/catalogDictionary', () => ({
  catalogDictionaryApi: {
    list: vi.fn(async () => ({ fields: {}, by_category: {}, brand_managed_fields: [] })), save,
  },
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
import { CatalogDictionarySection } from '../SettingsCatalogDictionary';

describe('dictionary category labels', () => {
  it('displays Sunglasses while keeping the registry and saved category code unchanged', async () => {
    render(<CatalogDictionarySection />);
    fireEvent.click(await screen.findByRole('button', { name: /Lens Colour/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Sunglasses' }));
    fireEvent.change(screen.getByPlaceholderText('Add an allowed value for Lens Colour…'), { target: { value: 'Green' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save list' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('lens_colour', ['Green'], 'SUNGLASS'));
    expect(registry).toMatchObject({ code: 'SUNGLASS', sku_prefix: 'SG', name: 'Sunglass' });
  });
});

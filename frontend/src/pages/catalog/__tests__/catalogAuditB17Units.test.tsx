// Catalogue audit batch b17: the shared rules under the editor.
//   CAT-01  useProductImages: a late upload / background removal never
//           reaches the form after discardPending (the form moved on).
//   CAT-07  attributes is a dictionary: no camelCase aliases manufactured by
//           the API client; a stored camelCase twin shows once, canonically.
//   CAT-05/06  Share Catalogue: only the current query owns the results; a
//           failed search says so (with Retry), never "No products match".

import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const api = vi.hoisted(() => ({
  uploadProductImage: vi.fn(),
  editProductImage: vi.fn(),
  rehostProductImage: vi.fn(),
  getProducts: vi.fn(),
}));
vi.mock('../../../services/api/products', () => ({ productApi: api }));
vi.mock('../../../services/api/cataloguePdf', () => ({
  cataloguePdfApi: { listTempCollections: vi.fn(async () => []) },
}));
vi.mock('../../../components/catalogue/CataloguePdfModal', () => ({ default: () => null }));

import { useProductImages } from '../quickadd/useProductImages';
import { addCamelAliases } from '../../../services/api/client';
import { canonicalAttributes, productToFormValues } from '../../../domain/catalog/productAdd';
import QuickSharePage from '../QuickSharePage';

const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

beforeEach(() => {
  for (const fn of [...Object.values(api), ...Object.values(toast)]) fn.mockReset();
});

describe('CAT-01: photo work belongs to the form that started it', () => {
  it('an upload finishing after discardPending is not appended (the audit probe)', async () => {
    const setImages = vi.fn();
    const up = deferred<{ url: string }>();
    api.uploadProductImage.mockReturnValue(up.promise);
    const { result } = renderHook(() => useProductImages(setImages));
    let run!: Promise<void>;
    act(() => { run = result.current.uploadImageFiles([new File(['x'], 'a.png', { type: 'image/png' })]); });
    expect(result.current.uploadingImages).toBe(true);
    act(() => result.current.discardPending()); // Save + New / next colour / another product
    expect(result.current.uploadingImages).toBe(false);
    await act(async () => { up.resolve({ url: '/api/v1/products/image/late-A' }); await run; });
    expect(setImages).not.toHaveBeenCalled();
    expect(result.current.uploadingImages).toBe(false);
  });

  it('a background removal finishing after discardPending does not replace the photo', async () => {
    const setImages = vi.fn();
    const ed = deferred<{ url: string }>();
    api.editProductImage.mockReturnValue(ed.promise);
    const { result } = renderHook(() => useProductImages(setImages));
    let run!: Promise<void>;
    act(() => { run = result.current.editImage('/api/v1/products/image/f1'); });
    expect(result.current.editingImages.size).toBe(1);
    act(() => result.current.discardPending());
    await act(async () => { ed.resolve({ url: '/api/v1/products/image/f1-clean' }); await run; });
    expect(setImages).not.toHaveBeenCalled();
    expect(result.current.editingImages.size).toBe(0);
  });

  it('same form: the upload is appended as before', async () => {
    const setImages = vi.fn();
    api.uploadProductImage.mockResolvedValue({ url: '/api/v1/products/image/ok' });
    const { result } = renderHook(() => useProductImages(setImages));
    await act(async () => { await result.current.uploadImageFiles([new File(['x'], 'a.png', { type: 'image/png' })]); });
    expect(setImages).toHaveBeenCalledTimes(1);
    expect(result.current.uploadingImages).toBe(false);
  });
});

describe('CAT-07: one spelling per attribute', () => {
  it('the client aliases transport keys but never inside attributes', () => {
    const out = addCamelAliases({
      product_id: 'P1',
      attributes: { brand_name: 'Ray-Ban', model_name: 'Aviator', colour_code: '001' },
      items: [{ offer_price: 10, attributes: { lens_size: '54' } }],
    });
    expect(out.productId).toBe('P1');
    expect(Object.keys(out.attributes)).toEqual(['brand_name', 'model_name', 'colour_code']);
    expect(out.items[0].offerPrice).toBe(10);
    expect(Object.keys(out.items[0].attributes)).toEqual(['lens_size']);
  });

  it('a stored camelCase twin: canonical wins when filled, fills it when blank; custom keys kept', () => {
    expect(
      canonicalAttributes({
        brand_name: 'Ray-Ban', brandName: 'Ray-Ban', // identical alias
        model_no: 'RB1', modelNo: 'RB9', // conflicting alias: the canonical one shows
        model_name: '', modelName: 'Aviator', // blank canonical: the twin fills it
        colourCode: '001', // only the twin
        'Frame Material': 'Metal', custom_note: 'x', // untouched
      }),
    ).toEqual({
      brand_name: 'Ray-Ban',
      model_no: 'RB1',
      model_name: 'Aviator',
      colour_code: '001',
      'Frame Material': 'Metal',
      custom_note: 'x',
    });
  });

  it('the edit form (and so its save) never carries a camelCase twin', () => {
    const v = productToFormValues({
      product_id: 'P1', category: 'SUNGLASS',
      attributes: { brand_name: 'Ray-Ban', brandName: 'Ray-Ban', model_name: '', modelName: 'Aviator' },
    } as never);
    expect(v.attributes).toMatchObject({ brand_name: 'Ray-Ban', model_name: 'Aviator' });
    expect(Object.keys(v.attributes || {}).filter((k) => /[A-Z]/.test(k))).toEqual([]);
  });
});

describe('CAT-05/06: Share Catalogue search', () => {
  const renderPage = () => render(<MemoryRouter><QuickSharePage /></MemoryRouter>);
  const box = () => screen.getByPlaceholderText(/Search by name, brand, SKU or category/);
  const row = (id: string, name: string) => ({ product_id: id, name });

  it('an older query answering last never replaces the newer query\'s results', async () => {
    const a = deferred<unknown>();
    const b = deferred<unknown>();
    api.getProducts.mockImplementation(({ search }: { search: string }) => (search === 'ray' ? a.promise : b.promise));
    renderPage();
    fireEvent.change(box(), { target: { value: 'ray' } });
    await waitFor(() => expect(api.getProducts).toHaveBeenCalledTimes(1), { timeout: 2000 });
    fireEvent.change(box(), { target: { value: 'oakley' } });
    await waitFor(() => expect(api.getProducts).toHaveBeenCalledTimes(2), { timeout: 2000 });
    await act(async () => { b.resolve({ products: [row('P-OAK', 'Oakley Holbrook')] }); });
    await act(async () => { a.resolve({ products: [row('P-RAY', 'Ray-Ban Aviator')] }); });
    expect(await screen.findByText('Oakley Holbrook')).toBeInTheDocument();
    expect(screen.queryByText('Ray-Ban Aviator')).toBeNull();
  });

  it('clearing the box while a search runs: its answer never reappears', async () => {
    const a = deferred<unknown>();
    api.getProducts.mockReturnValue(a.promise);
    renderPage();
    fireEvent.change(box(), { target: { value: 'ray' } });
    await waitFor(() => expect(api.getProducts).toHaveBeenCalledTimes(1), { timeout: 2000 });
    fireEvent.change(box(), { target: { value: '' } });
    await act(async () => { a.resolve({ products: [row('P-RAY', 'Ray-Ban Aviator')] }); });
    expect(screen.queryByText('Ray-Ban Aviator')).toBeNull();
    expect(screen.queryByText(/Searching/)).toBeNull();
  });

  it('a failed search says it failed and offers Retry; only a real empty answer says no match', async () => {
    api.getProducts.mockRejectedValueOnce(new Error('500'));
    renderPage();
    fireEvent.change(box(), { target: { value: 'ray' } });
    expect(await screen.findByRole('alert', {}, { timeout: 2000 })).toHaveTextContent(/Search failed/);
    expect(screen.queryByText(/No products match/)).toBeNull();
    api.getProducts.mockResolvedValueOnce({ products: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/No products match/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(api.getProducts).toHaveBeenCalledTimes(2);
  });
});

// ============================================================================
// Catalogue editor audit batch b17 (L1-F44, L1-F45, CAT-01, CAT-03, CAT-04)
// ============================================================================
// L1-F44 / CAT-02  a cleared description is SENT (''), never omitted.
// L1-F45 / CAT-01  no save door runs while a photo is uploading; a photo that
//                  finishes after the form moved on never lands on the next one.
// CAT-03           the ?edit= address stays, so a refresh reopens the product;
//                  leaving it (sidebar Add product) drops back to a create form.
// CAT-04           Cancel with unsaved changes asks first.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Mansi', roles: ['CATALOG_MANAGER'], activeStoreId: '', storeIds: [] },
    hasRole: (r: string | string[]) => (Array.isArray(r) ? r : [r]).includes('CATALOG_MANAGER'),
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const product = (id: string, extra: Record<string, unknown> = {}) => ({
  product_id: id,
  sku: `SG-RAYBAN-${id}`,
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: id, lens_size: '54' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  description: 'Old copy for the website',
  images: ['/api/v1/products/image/old-photo'],
  ...extra,
});

const known: Record<string, (...a: unknown[]) => unknown> = {
  getCategoryRegistry: async () => { throw new Error('offline'); },
  getBrandOptions: async () => ({ brands: [] }),
  getProduct: async (id: unknown) => product(String(id)),
  getProducts: async () => ({ products: [], total_count: 0 }),
  updateProduct: async () => ({}),
  createProduct: async () => ({ product_id: 'P-NEW', sku: 'SG-RAYBAN-RB4165-601' }),
  previewSku: async () => ({ category: 'SUNGLASS', sku: 'SG-RAYBAN', taken: false, identity_fields: [] }),
  getWebsiteVerdict: async () => ({ online: true, reason: null }),
};
const productSpies = vi.hoisted(() => ({}) as Record<string, ReturnType<typeof vi.fn>>);
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: new Proxy({}, {
    get: (_t, key: string) => (productSpies[key] ??= vi.fn(known[key] ?? (async () => ({})))),
  }),
}));
const http = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: {} })),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: {} })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: {},
  catalogBulkApi: { fixFlags: vi.fn() },
}));
// The strip's two doors, as plain buttons: a sibling chip and the exact-match "Open it".
vi.mock('../SimilarProductsHint', () => ({
  SimilarProductsHint: (p: { onPickSibling: (id: string) => void; onOpenExisting: (e: unknown) => void }) => (
    <>
      <button type="button" onClick={() => p.onPickSibling('P-SIB')}>sibling chip</button>
      <button type="button" onClick={() => p.onOpenExisting({ product_id: 'P-SIB' })}>open exact match</button>
    </>
  ),
}));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';

function Where() {
  const l = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="where">{l.pathname + l.search}</output>
      {/* What the sidebar's "Add product" link does. */}
      <button type="button" onClick={() => navigate('/catalog/add')}>sidebar add</button>
      {/* Any other sidebar menu link. */}
      <Link to="/stock">sidebar stock</Link>
      {/* The top command palette's Catalog result (a programmatic navigate). */}
      <button type="button" onClick={() => navigate('/catalog')}>palette catalog</button>
      {/* A direct switch to another product on the same page. */}
      <button type="button" onClick={() => navigate('/catalog/add?edit=P-B')}>open P-B</button>
    </>
  );
}
const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/catalog/add" element={<><QuickAddPage /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
const where = () => screen.getByTestId('where').textContent || '';
const sleep = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const pickPhoto = (name: string) =>
  fireEvent.change(fileInput(), { target: { files: [new File(['x'], name, { type: 'image/png' })] } });
/** An upload that answers only when the test says so (a slow connection). */
const holdUploads = () => {
  const release: Array<(v: unknown) => void> = [];
  productSpies.uploadProductImage.mockImplementation(
    (f: unknown) =>
      new Promise((r) => {
        release.push(() => r({ url: `/api/v1/products/image/${(f as File).name}` }));
      }),
  );
  return release;
};

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of [...Object.values(http), ...Object.values(productSpies), ...Object.values(toast)]) fn.mockClear();
  for (const k of [...Object.keys(known), 'uploadProductImage']) productSpies[k] ??= vi.fn();
  for (const [k, fn] of Object.entries(productSpies)) fn.mockImplementation(known[k] ?? (async () => ({})));
  window.sessionStorage.clear();
});

describe('L1-F44 / CAT-02: clearing the description', () => {
  it('sends an empty description, so the old copy is not kept', async () => {
    renderAt('/catalog/add?edit=P-A');
    const box = (await screen.findByPlaceholderText(/Optional product description/)) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe('Old copy for the website'));
    fireEvent.change(box, { target: { value: '' } });
    await sleep(30);
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    const body = productSpies.updateProduct.mock.calls[0][1] as Record<string, unknown>;
    expect(body).toHaveProperty('description', '');
  });

  it('an untouched description is sent as it was (an unrelated edit keeps it)', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9990' } });
    await sleep(30);
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    expect(productSpies.updateProduct.mock.calls[0][1]).toMatchObject({ description: 'Old copy for the website' });
  });
});

describe('L1-F45 / CAT-01: no save while a photo is uploading', () => {
  it('Save, Ctrl+Enter wait; after the upload the save carries the new photo', async () => {
    const release = holdUploads();
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    pickPhoto('new-photo.png');
    await waitFor(() => expect(screen.getByRole('button', { name: /Save changes/ })).toBeDisabled());
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    await sleep(30);
    expect(productSpies.updateProduct).not.toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/still uploading/));
    await act(async () => { release[0](undefined); });
    await waitFor(() => expect(screen.getByRole('button', { name: /Save changes/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    expect(productSpies.updateProduct.mock.calls[0][1]).toMatchObject({
      images: ['/api/v1/products/image/old-photo', '/api/v1/products/image/new-photo.png'],
    });
  });

  it('two uploads at once: the first finishing does not unlock Save', async () => {
    const release = holdUploads();
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    pickPhoto('one.png');
    pickPhoto('two.png');
    await waitFor(() => expect(release.length).toBe(2));
    await act(async () => { release[0](undefined); });
    await sleep(30);
    expect(screen.getByRole('button', { name: /Save changes/ })).toBeDisabled();
    await act(async () => { release[1](undefined); });
    await waitFor(() => expect(screen.getByRole('button', { name: /Save changes/ })).toBeEnabled());
  });

  it('a photo that finishes after the form moved on is not added to the new form', async () => {
    const release = holdUploads();
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    pickPhoto('photo-of-A.png');
    await waitFor(() => expect(release.length).toBe(1));
    // A photo still uploading asks first (CAT-04); the staff member leaves.
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'sidebar add' }));
    await screen.findByRole('button', { name: /Save product/ });
    expect(confirm).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
    await act(async () => { release[0](undefined); });
    await sleep(30);
    expect(document.querySelector('img[src*="photo-of-A"]')).toBeNull();
    expect(toast.warning).toHaveBeenCalledWith(expect.stringMatching(/after you moved on/));
    // Not uploading any more: the new form can be saved.
    expect(screen.getByRole('button', { name: /Save product/ })).toBeEnabled();
  });
});

describe('CAT-03: the edit address names the product', () => {
  it('keeps ?edit= after the load, so a refresh reopens the same product', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    expect(new URLSearchParams(where().split('?')[1]).get('edit')).toBe('P-A');
  });

  it('a product that will not load stays an edit address, says so on screen, cannot be saved', async () => {
    productSpies.getProduct.mockRejectedValue(Object.assign(new Error('500'), { response: { status: 500 } }));
    renderAt('/catalog/add?edit=P-A');
    const panel = await screen.findByRole('alert');
    expect(panel.textContent).toMatch(/Could not open product P-A/);
    await sleep(30);
    expect(where()).toBe('/catalog/add?edit=P-A');
    // No editable create form and no Save behind the message.
    expect(screen.queryByRole('button', { name: /Save product/ })).toBeNull();
    expect(screen.queryByLabelText(/^MRP/)).toBeNull();
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    await sleep(30);
    expect(productSpies.createProduct).not.toHaveBeenCalled();
    expect(productSpies.updateProduct).not.toHaveBeenCalled();
    // The server is back: Try again opens the product for editing.
    productSpies.getProduct.mockImplementation(async (id: unknown) => product(String(id)));
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByRole('button', { name: /Save changes/ });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a product id that does not exist: no "try again", a way out to a new product', async () => {
    productSpies.getProduct.mockRejectedValue(Object.assign(new Error('404'), { response: { status: 404 } }));
    renderAt('/catalog/add?edit=DOES-NOT-EXIST');
    const panel = await screen.findByRole('alert');
    expect(panel.textContent).toMatch(/No product has this id/);
    expect(panel.textContent).not.toMatch(/[Rr]efresh/);
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add a new product instead' }));
    const save = await screen.findByRole('button', { name: /Save product/ });
    expect(where()).toBe('/catalog/add');
    expect(save).toBeEnabled();
  });

  it('leaving the address (sidebar Add product) opens a blank create form', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    fireEvent.click(screen.getByRole('button', { name: 'sidebar add' }));
    await screen.findByRole('button', { name: /Save product/ });
    expect(where()).toBe('/catalog/add');
  });
});

describe('CAT-04: Cancel asks before dropping unsaved changes', () => {
  it('unsaved change + Cancel -> confirm; staying keeps the page', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9990' } });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const cancels = screen.getAllByRole('button', { name: 'Cancel' });
    fireEvent.click(cancels[cancels.length - 1]);
    expect(confirm).toHaveBeenCalledTimes(1);
    await sleep(30);
    expect(where()).toMatch(/^\/catalog\/add\?edit=P-A/);
    confirm.mockReturnValue(true);
    fireEvent.click(cancels[0]);
    await waitFor(() => expect(where()).toMatch(/^\/catalog\?/));
    confirm.mockRestore();
  });

  it('nothing changed: Cancel leaves without asking', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);
    await waitFor(() => expect(where()).toMatch(/^\/catalog\?/));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

describe('CAT-04: refresh and other menus ask before dropping unsaved changes', () => {
  const dirtyEdit = async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9990' } });
    await sleep(30);
  };
  const unload = () => {
    const ev = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(ev);
    return ev.defaultPrevented;
  };

  it('refresh / tab close with an unsaved change is held by the browser prompt', async () => {
    await dirtyEdit();
    expect(unload()).toBe(true);
  });

  it('nothing changed: refresh is not held', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    expect(unload()).toBe(false);
  });

  it('another menu link asks; staying keeps the page', async () => {
    await dirtyEdit();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('link', { name: 'sidebar stock' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await sleep(30);
    expect(where()).toMatch(/^\/catalog\/add\?edit=P-A/);
    confirm.mockRestore();
  });

  it('"Add product" on the edit page asks; staying keeps the typed edit (no reload)', async () => {
    await dirtyEdit();
    const loads = productSpies.getProduct.mock.calls.length;
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'sidebar add' }));
    await sleep(50);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-A');
    expect((screen.getByLabelText(/^MRP/) as HTMLInputElement).value).toBe('9990');
    expect(screen.getByRole('button', { name: /Save changes/ })).toBeInTheDocument();
    expect(productSpies.getProduct.mock.calls.length).toBe(loads);
    // The kept edit still saves to the same product.
    confirm.mockRestore();
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    expect(productSpies.updateProduct.mock.calls[0][0]).toBe('P-A');
    expect(productSpies.updateProduct.mock.calls[0][1]).toMatchObject({ mrp: 9990 });
  });

  it('"Add product" on the edit page, confirmed: a blank create form', async () => {
    await dirtyEdit();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'sidebar add' }));
    await screen.findByRole('button', { name: /Save product/ });
    expect(where()).toBe('/catalog/add');
    expect((screen.getByLabelText(/^MRP/) as HTMLInputElement).value).toBe('');
    confirm.mockRestore();
  });
});

describe('CAT-04 / D51: every exit asks, in every form mode', () => {
  const unload = () => {
    const ev = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(ev);
    return ev.defaultPrevented;
  };
  const dirtyEdit = async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '7990' } });
    await sleep(30);
  };

  it('the command palette (programmatic navigate) asks; Stay keeps 7990', async () => {
    await dirtyEdit();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'palette catalog' }));
    await sleep(30);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-A');
    expect((screen.getByLabelText(/^MRP/) as HTMLInputElement).value).toBe('7990');
    confirm.mockRestore();
  });

  it('a switch to another product on the same page asks; Stay never loads it over the edit', async () => {
    await dirtyEdit();
    const loads = productSpies.getProduct.mock.calls.length;
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'open P-B' }));
    await sleep(50);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-A');
    expect(productSpies.getProduct.mock.calls.length).toBe(loads);
    expect((screen.getByLabelText(/^MRP/) as HTMLInputElement).value).toBe('7990');
    confirm.mockRestore();
  });

  it('a new product with a typed MRP: refresh is held and a menu asks', async () => {
    renderAt('/catalog/add');
    await screen.findByRole('button', { name: /Save product/ });
    await sleep(30);
    expect(unload()).toBe(false);
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9876' } });
    await sleep(30);
    expect(unload()).toBe(true);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'palette catalog' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add');
    confirm.mockRestore();
  });

  it('a sibling chip while editing asks; Stay keeps ?edit=P-A and the typed MRP', async () => {
    await dirtyEdit();
    const loads = productSpies.getProduct.mock.calls.length;
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await sleep(50);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-A');
    expect((screen.getByLabelText(/^MRP/) as HTMLInputElement).value).toBe('7990');
    expect(screen.getByRole('button', { name: /Save changes/ })).toBeInTheDocument();
    expect(productSpies.getProduct.mock.calls.length).toBe(loads);
    confirm.mockRestore();
  });

  it('a sibling chip while editing, confirmed: a new variant of the sibling', async () => {
    await dirtyEdit();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await waitFor(() => expect(where()).toBe('/catalog/add'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(productSpies.getProduct).toHaveBeenLastCalledWith('P-SIB');
    confirm.mockRestore();
  });

  it('a sibling chip on an unchanged edit does not ask', async () => {
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await waitFor(() => expect(where()).toBe('/catalog/add'));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('the exact-match "Open it" while editing asks; Stay keeps the edit', async () => {
    await dirtyEdit();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'open exact match' }));
    await sleep(30);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(where()).toBe('/catalog/add?edit=P-A');
    expect((screen.getByLabelText(/^MRP/) as HTMLInputElement).value).toBe('7990');
    confirm.mockRestore();
  });

  it('a successful save leaves without a redundant question', async () => {
    await dirtyEdit();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(where()).not.toMatch(/edit=P-A/));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

// D89(1): on a NEW product the form's own doors ask too while it holds typed
// work (a changed field or an added photo); a clean create form moves freely.
describe('CAT-04 / D89(1): a new product with typed work asks at the form\'s own doors', () => {
  const val = (label: RegExp) => (screen.getByLabelText(label) as HTMLInputElement).value;
  const openCreate = async () => {
    renderAt('/catalog/add');
    await screen.findByRole('button', { name: /Save product/ });
    fireEvent.click(screen.getByText('Sunglasses'));
    await screen.findByRole('button', { name: 'sibling chip' });
    await sleep(30);
  };
  const typeCreate = async () => {
    await openCreate();
    fireEvent.change(screen.getByLabelText(/^Brand Name/), { target: { value: 'Ray-Ban' } });
    fireEvent.change(screen.getByLabelText(/^Model No/), { target: { value: 'RB4165' } });
    fireEvent.change(screen.getByLabelText(/^Colour Code/), { target: { value: '601' } });
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9876' } });
    await sleep(30);
  };
  const keptTyping = () => {
    expect(where()).toBe('/catalog/add');
    expect(val(/^MRP/)).toBe('9876');
    expect(val(/^Colour Code/)).toBe('601');
    expect(screen.getByRole('button', { name: /Save product/ })).toBeInTheDocument();
  };
  /** Save meets an existing product: the duplicate popup opens over the typed form. */
  const dupPopup = async () => {
    await typeCreate();
    const { DuplicateProductError } = await import('../../../services/api/products');
    productSpies.createProduct.mockRejectedValue(
      Object.assign(new DuplicateProductError('exists'), {
        existing: { product_id: 'P-OLD', sku: 'SG-RAYBAN-OLD', is_active: true },
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: /Save product/ }));
    await screen.findByRole('button', { name: /Open the existing product/ });
  };

  it('exact-match "Open it" asks; Cancel keeps every typed value', async () => {
    await typeCreate();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'open exact match' }));
    await sleep(30);
    expect(confirm).toHaveBeenCalledTimes(1);
    keptTyping();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'open exact match' }));
    await waitFor(() => expect(where()).not.toBe('/catalog/add'));
    confirm.mockRestore();
  });

  it('sibling chip asks; Cancel keeps the typing and never loads the sibling', async () => {
    await typeCreate();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await sleep(50);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(productSpies.getProduct).not.toHaveBeenCalledWith('P-SIB');
    keptTyping();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalledWith('P-SIB'));
    confirm.mockRestore();
  });

  it('duplicate popup "Open the existing product" asks; Cancel keeps the typing', async () => {
    await dupPopup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: /Open the existing product/ }));
    await sleep(30);
    expect(confirm).toHaveBeenCalledTimes(1);
    keptTyping();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: /Open the existing product/ }));
    await waitFor(() => expect(where()).not.toBe('/catalog/add'));
    confirm.mockRestore();
  });

  it('duplicate popup "Add as variant" asks; Cancel keeps the typing and loads nothing', async () => {
    await dupPopup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const variantBtn = screen.getByRole('button', { name: /Add a new colour.size of this model/ });
    fireEvent.click(variantBtn);
    await sleep(50);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(productSpies.getProduct).not.toHaveBeenCalledWith('P-OLD');
    keptTyping();
    confirm.mockReturnValue(true);
    fireEvent.click(variantBtn);
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalledWith('P-OLD'));
    confirm.mockRestore();
  });

  it('a photo alone on a new form is typed work: the sibling chip asks; Cancel keeps the photo', async () => {
    const release = holdUploads();
    await openCreate();
    pickPhoto('front.png');
    await waitFor(() => expect(release.length).toBe(1));
    await act(async () => { release[0](undefined); });
    await waitFor(() => expect(document.querySelector('img[src*="front.png"]')).not.toBeNull());
    await sleep(30);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await sleep(50);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(productSpies.getProduct).not.toHaveBeenCalledWith('P-SIB');
    expect(document.querySelector('img[src*="front.png"]')).not.toBeNull();
    confirm.mockRestore();
  });

  it('a clean new form moves without a question (chip and Open it)', async () => {
    await openCreate();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'sibling chip' }));
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalledWith('P-SIB'));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('a clean new form: exact-match "Open it" moves without a question', async () => {
    await openCreate();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'open exact match' }));
    await waitFor(() => expect(where()).not.toBe('/catalog/add'));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

// ============================================================================
// Product photos in order (owner 2026-10-12, mockup artboard "Photos")
// ============================================================================
// "Photo 1 is the main photo on the website and at the till." On the add /
// edit page each photo shows its place and photo 1 a 'Main photo' badge; a
// photo moves by DRAG (mouse) or by its Earlier / Later buttons (the tablets
// are touch screens, where HTML5 drag is unreliable); an angle guide sits
// above the photos. The order IS the images array the page saves.

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Avinash', roles: ['ADMIN'], activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const A = '/api/v1/products/image/front';
const B = '/api/v1/products/image/angled';
const C = '/api/v1/products/image/side';

const PRODUCT = {
  product_id: 'P-SRC',
  sku: 'SG-RAYB-4165-001',
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '001', lens_size: '54' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  images: [A, B, C],
};

const known: Record<string, (...a: unknown[]) => unknown> = {
  getCategoryRegistry: async () => { throw new Error('offline'); },
  getBrandOptions: async () => ({ brands: [] }),
  getProduct: async () => PRODUCT,
  // An upload of the file named 'front' comes back as A, and so on.
  uploadProductImage: async (f: unknown) => ({ url: `/api/v1/products/image/${(f as File).name}` }),
  // The catalog list (GET /products): photo 1 is C; an old single image
  // field still names A.
  getProducts: async () => ({ products: [{ ...PRODUCT, images: [C, A], image_url: A }], total_count: 1 }),
};
const productSpies = vi.hoisted(() => ({}) as Record<string, ReturnType<typeof vi.fn>>);
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: new Proxy({}, {
    get: (_t, key: string) => (productSpies[key] ??= vi.fn(known[key] ?? (async () => ({})))),
  }),
  catalogApi: { getOnlineSummary: vi.fn(async () => ({ catalog: null })) },
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: {
    get: vi.fn(async () => ({ data: {} })),
    put: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async () => ({ data: {} })),
    patch: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
// The review editor (?review=) loads and saves through the catalog door.
const catalogSpies = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  promoteDryRun: vi.fn(),
  list: vi.fn(),
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: catalogSpies,
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { CatalogRequestError } from '../../../services/api/catalog';
import { movePhoto, useProductImages } from '../quickadd/useProductImages';
import { AngleGuide, ProductPhotoGrid } from '../quickadd/ProductPhotoGrid';
import { CatalogManagerPage } from '../CatalogManagerPage';
import { QuickAddPage } from '../QuickAddPage';

/** An imported product waiting in the review queue (photos A, B, C). */
const REVIEW_DOC = {
  id: 'CAT-1',
  sku: 'SG-RAYB-4165-001',
  title: 'Ray-Ban RB4165',
  brand: 'Ray-Ban',
  category: 'SUNGLASS',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165' },
  pricing: { mrp: 7890, offer_price: 7890 },
  hsn_code: '900410',
  gst_rate: 18,
  images: [A, B, C],
  needs_review: true,
  pos_ready: false,
  updated_at: '2026-10-12T10:00:00+05:30',
};

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of Object.values(productSpies)) fn.mockClear();
  catalogSpies.get.mockReset().mockResolvedValue(REVIEW_DOC);
  catalogSpies.update.mockReset().mockImplementation(async (_id: string, body: object) => ({
    product: { ...REVIEW_DOC, ...body },
  }));
  catalogSpies.promoteDryRun.mockReset().mockResolvedValue({ ok: true, gaps: [], duplicate_warnings: [] });
  catalogSpies.list.mockReset().mockResolvedValue({ products: [], total: 0 });
  window.sessionStorage.clear();
});

/** The grid with the page's own move handler (useProductImages.moveImage,
 *  the one InventorySection passes as onMove) - no copy of the wiring. */
function Harness({ initial }: { initial: string[] }) {
  const [images, setImages] = useState(initial);
  const { moveImage } = useProductImages(setImages);
  return (
    <ProductPhotoGrid
      images={images}
      onMove={moveImage}
      onRemove={() => {}}
      onEdit={() => {}}
      editing={new Set()}
    />
  );
}

/** The photos as the screen shows them, in order. */
const shown = () => screen.getAllByRole('img').map((img) => img.getAttribute('src'));
const tiles = () => screen.getAllByRole('listitem');
const btn = (name: string) => screen.getByRole('button', { name });
const tile = (n: number) => screen.getByAltText(`Photo ${n}`).closest('li') as HTMLElement;

/** A mouse drag of photo `from` onto photo `to` (numbered as the screen
 *  numbers them). It checks the two gates a real browser applies and a
 *  synthetic event skips: the tile must be draggable, and its dragover must
 *  be cancelled, or the browser never fires the drop at all. */
function dragPhoto(from: number, to: number) {
  const source = tile(from);
  const target = tile(to);
  const data = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
  expect(source).toHaveAttribute('draggable', 'true');
  fireEvent.dragStart(source, { dataTransfer: data });
  expect(fireEvent.dragOver(target, { dataTransfer: data })).toBe(false);
  fireEvent.drop(target, { dataTransfer: data });
  fireEvent.dragEnd(source, { dataTransfer: data });
}

describe('movePhoto - the one move rule', () => {
  it('moves the first, a middle and the last photo', () => {
    expect(movePhoto([A, B, C], 0, 2)).toEqual([B, C, A]);
    expect(movePhoto([A, B, C], 1, 0)).toEqual([B, A, C]);
    expect(movePhoto([A, B, C], 2, 0)).toEqual([C, A, B]);
    expect(movePhoto([A, B, C], 2, 1)).toEqual([A, C, B]);
  });

  it('leaves the array alone for a no-op or an out-of-range move', () => {
    const list = [A, B, C];
    expect(movePhoto(list, 1, 1)).toBe(list);
    expect(movePhoto(list, 0, -1)).toBe(list);
    expect(movePhoto(list, 3, 0)).toBe(list);
  });
});

describe('the photo grid', () => {
  it('numbers every photo and badges only photo 1 as the main photo', () => {
    render(<Harness initial={[A, B, C]} />);
    tiles().forEach((tile, i) => expect(within(tile).getByText(String(i + 1))).toBeInTheDocument());
    expect(screen.getAllByText('Main photo')).toHaveLength(1);
    expect(within(tiles()[0]).getByText('Main photo')).toBeInTheDocument();
  });

  it("disables the first photo's Earlier and the last photo's Later; the rest work", () => {
    render(<Harness initial={[A, B, C]} />);
    expect(btn('Move photo 1 earlier')).toBeDisabled();
    expect(btn('Move photo 3 later')).toBeDisabled();
    for (const name of ['Move photo 1 later', 'Move photo 2 earlier', 'Move photo 2 later', 'Move photo 3 earlier']) {
      expect(btn(name)).toBeEnabled();
    }
  });

  it('Earlier and Later are real 40px buttons', () => {
    render(<Harness initial={[A, B]} />);
    for (const name of ['Move photo 1 later', 'Move photo 2 earlier']) {
      expect(btn(name).tagName).toBe('BUTTON');
      expect(btn(name).className).toMatch(/\bmin-h-10\b/);
    }
  });

  // The numbers and the disabled ends belong to the PLACE, not the photo:
  // after a move they must read the new order, not the order the page opened in.
  it('after a move the numbers and the disabled ends follow the new order', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[A, B, C]} />);
    const expectPlaces = () => {
      tiles().forEach((t, i) => expect(within(t).getByText(String(i + 1))).toBeInTheDocument());
      expect(btn('Move photo 1 earlier')).toBeDisabled();
      expect(btn('Move photo 3 later')).toBeDisabled();
      for (const name of ['Move photo 1 later', 'Move photo 2 earlier', 'Move photo 2 later', 'Move photo 3 earlier']) {
        expect(btn(name)).toBeEnabled();
      }
    };
    await user.click(btn('Move photo 3 earlier'));
    expect(shown()).toEqual([A, C, B]);
    expectPlaces();
    await user.click(btn('Move photo 1 later'));
    expect(shown()).toEqual([C, A, B]);
    expectPlaces();
  });

  it('moves the first, a middle and the last photo with the buttons', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[A, B, C]} />);
    await user.click(btn('Move photo 1 later'));
    expect(shown()).toEqual([B, A, C]);
    await user.click(btn('Move photo 2 earlier'));
    expect(shown()).toEqual([A, B, C]);
    await user.click(btn('Move photo 3 earlier'));
    expect(shown()).toEqual([A, C, B]);
    // The badge follows the place, not the photo.
    expect(within(tiles()[0]).getByText('Main photo')).toBeInTheDocument();
  });

  it('a drag and the buttons give the same array', async () => {
    const user = userEvent.setup();
    // last -> first
    const dragged = render(<Harness initial={[A, B, C]} />);
    dragPhoto(3, 1);
    const byDrag = shown();
    dragged.unmount();
    render(<Harness initial={[A, B, C]} />);
    await user.click(btn('Move photo 3 earlier'));
    await user.click(btn('Move photo 2 earlier'));
    expect(byDrag).toEqual([C, A, B]);
    expect(shown()).toEqual(byDrag);
  });

  it('a drag from first to last matches Later twice', async () => {
    const user = userEvent.setup();
    const dragged = render(<Harness initial={[A, B, C]} />);
    dragPhoto(1, 3);
    const byDrag = shown();
    dragged.unmount();
    render(<Harness initial={[A, B, C]} />);
    await user.click(btn('Move photo 1 later'));
    await user.click(btn('Move photo 2 later'));
    expect(byDrag).toEqual([B, C, A]);
    expect(shown()).toEqual(byDrag);
  });

  it('a file dropped on a photo does not move anything', () => {
    render(<Harness initial={[A, B, C]} />);
    const file = { files: [new File(['x'], 'x.png')] };
    expect(fireEvent.dragOver(tiles()[0], { dataTransfer: file })).toBe(false);
    // A cancelled drop is what stops the browser opening the image and losing the form.
    expect(fireEvent.drop(tiles()[0], { dataTransfer: file })).toBe(false);
    expect(shown()).toEqual([A, B, C]);
  });
});

describe('the angle guide', () => {
  it("says the owner's angle order, word for word", () => {
    render(<AngleGuide />);
    expect(screen.getByText('Angle order for every product:')).toBeInTheDocument();
    for (const step of ['1 Front', '2 Angled (3/4)', '3 Side', '4 Back / temples', '5 Folded / in case']) {
      expect(screen.getByText(step)).toBeInTheDocument();
    }
  });
});

describe('the catalog list thumbnail', () => {
  it('is photo 1 even when an old single image field disagrees', async () => {
    render(
      <MemoryRouter initialEntries={['/catalog']}>
        <CatalogManagerPage />
      </MemoryRouter>,
    );
    const thumb = await screen.findByRole('button', { name: 'View images of Ray-Ban RB4165' });
    expect(within(thumb).getByRole('img')).toHaveAttribute('src', C);
  });
});

describe('the add / edit page saves the new order', () => {
  it('reorders with the buttons and saves the images array in that order', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/catalog/add?edit=P-SRC']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalled());
    await screen.findByRole('button', { name: 'Move photo 3 earlier' });
    expect(screen.getByText('Angle order for every product:')).toBeInTheDocument();

    await user.click(btn('Move photo 3 earlier'));
    await user.click(btn('Move photo 2 earlier'));
    await user.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    const [id, body] = productSpies.updateProduct.mock.calls[0] as [string, { images: string[] }];
    expect(id).toBe('P-SRC');
    expect(body.images).toEqual([C, A, B]);
  });

  // Photo 3 dragged onto photo 1 must save what Earlier twice saves above,
  // [C, A, B]. A swap would give [C, B, A]; adjacent moves cannot tell the
  // two apart, so this is a long move through the real page wiring.
  it('a drag from photo 3 onto photo 1 saves the same order as Earlier twice', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/catalog/add?edit=P-SRC']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await screen.findByRole('button', { name: 'Move photo 3 earlier' });

    dragPhoto(3, 1);
    await user.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    const [, body] = productSpies.updateProduct.mock.calls[0] as [string, { images: string[] }];
    expect(body.images).toEqual([C, A, B]);
  });

  // The first save of a NEW product (no ?edit=) - the catalogue rebuild path.
  // Photo 1 on screen must be photo 1 in the create body.
  it('a new product is created with its photos in the order the grid shows', async () => {
    const user = userEvent.setup();
    const fill = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
    render(
      <MemoryRouter initialEntries={['/catalog/add']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await user.click(screen.getByText('Sunglasses'));
    fill(/^Brand Name/, 'Ray-Ban');
    fill(/^Model No/, 'RB4165');
    fill(/^Colour Code/, '601');
    fill(/^MRP/, '7890');
    await user.upload(
      screen.getByTitle('Upload product images'),
      ['front', 'angled', 'side'].map((n) => new File(['x'], n, { type: 'image/png' })),
    );
    await screen.findByRole('button', { name: 'Move photo 3 earlier' });
    const grid = () => screen.getAllByAltText(/^Photo \d+$/).map((img) => img.getAttribute('src'));
    expect(grid()).toEqual([A, B, C]);

    await user.click(btn('Move photo 3 earlier'));
    await user.click(btn('Move photo 2 earlier'));
    expect(grid()).toEqual([C, A, B]);
    await user.click(screen.getByRole('button', { name: /Save product/ }));

    await waitFor(() => expect(productSpies.createProduct).toHaveBeenCalledTimes(1));
    const [body] = productSpies.createProduct.mock.calls[0] as [{ images: string[] }];
    expect(body.images).toEqual([C, A, B]);
  });
});

describe('the review editor offers the same reorder', () => {
  const openReview = async () => {
    render(
      <MemoryRouter initialEntries={['/catalog/add?review=CAT-1']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await screen.findByRole('button', { name: 'Move photo 3 earlier' });
  };

  it('a pure reorder is a change: Save fixes sends the photos in the new order', async () => {
    const user = userEvent.setup();
    await openReview();

    await user.click(btn('Move photo 3 earlier'));
    await user.click(screen.getByRole('button', { name: /Save fixes/ }));

    await waitFor(() => expect(catalogSpies.update).toHaveBeenCalledTimes(1));
    const [id, body] = catalogSpies.update.mock.calls[0] as [string, { images: string[] }];
    expect(id).toBe('CAT-1');
    expect(body.images).toEqual([A, C, B]);
  });

  it('when someone else saved first, the reload keeps the reorder for the next save', async () => {
    const user = userEvent.setup();
    const conflict = new CatalogRequestError('changed by someone else');
    (conflict as unknown as { status: number }).status = 409;
    catalogSpies.update.mockRejectedValueOnce(conflict);
    await openReview();

    await user.click(btn('Move photo 3 earlier'));
    await user.click(screen.getByRole('button', { name: /Save fixes/ }));
    await waitFor(() => expect(catalogSpies.get).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('button', { name: /Save fixes/ }));

    await waitFor(() => expect(catalogSpies.update).toHaveBeenCalledTimes(2));
    const [, body] = catalogSpies.update.mock.calls[1] as [string, { images: string[] }];
    expect(body.images).toEqual([A, C, B]);
  });
});
